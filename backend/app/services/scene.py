"""场景化配置模板业务规则。

核心机制：
- 发布模板时把当时的预设指标、推荐图表、默认参数、适用场景整体冻结成
  「指标图表配置快照」，生成不可变的发布版本；之后修改模板只能产生新版本，
  已经冻结的版本与基于它创建的实例不受影响（避免实例化结果漂移）。
- 导出包随包携带快照、默认参数与适用场景，采用确定性打包，同一冻结版本
  重复导出的内容完全一致。
- 分析入口实例化只读取冻结版本里的实例化参数；同一模板的并发实例化请求
  在锁内选取「首个冻结版本」，因此两个同时开始的请求得到同一套参数。
"""
from __future__ import annotations

import copy
import io
import json
import threading
import zipfile
from datetime import datetime, timezone
from typing import Any

from app.store import store

MODULE = "scene"
RELEASE_MODULE = "scene_release"
PACKAGE_MODULE = "scene_package"
INSTANCE_MODULE = "scene_instance"

REQUIRED_FIELDS = ["模板编号", "模板名称", "适用场景"]
SNAPSHOT_FIELDS = ["适用场景", "预设指标", "推荐图表", "默认参数"]
EDITABLE_FIELDS = ["模板名称", "适用场景", "预设指标", "推荐图表", "默认参数", "模板描述"]
STATUS_ORDER = ["草稿", "已发布", "已下线", "已归档"]
ACTION_RULES = {"发布模板": "已发布", "下线模板": "已发布", "归档模板": "已下线"}
NEGATIVE_ACTIONS = ["下线模板"]

# 冻结时间统一使用 UTC；打包过程不依赖本机时区，保证不同机器导出内容一致。
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _json_dumps(payload: Any) -> str:
    """稳定的 JSON 序列化：键排序、不转义中文，保证重复导出字节一致。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SceneService:
    """场景模板、冻结版本、导出包与实例化的统一入口。"""

    def __init__(self) -> None:
        # 按模板编号加锁：并发实例化时在锁内确定首个冻结版本。
        self._instantiate_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # 全局写锁：保证版本/导出包/实例的 ID 生成与追加是原子操作。
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------ 模板
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._with_summary(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_summary(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("预设指标", "推荐图表", "默认参数", "模板描述"):
            entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_summary(entry), []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """修改模板内容。

        已有冻结版本的模板被修改后回到草稿态，需要重新发布才会产生新版本；
        旧冻结版本保持不变，旧实例继续使用旧快照（华南不会被华东覆盖）。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"场景模板 {entry_id} 不存在或已归档"
        if entry.get("status") == STATUS_ORDER[-1]:
            return None, "已归档模板不允许再修改，请新建模板"
        for field in EDITABLE_FIELDS:
            if field in values:
                entry[field] = values[field]
        if self._releases(entry["模板编号"]):
            # 已有冻结版本：改动只作用于工作副本，发布后另开新版本。
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
        return self._with_summary(entry), "场景模板已更新，重新发布后生成新版本"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"场景模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于场景化配置模板可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "发布模板":
            release, message = self.publish_entry(entry_id)
            if release is None:
                return None, message
            return self._with_summary(entry), message
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._with_summary(entry), f"场景模板已{action}"

    # ------------------------------------------------------------ 冻结与版本
    def publish_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """发布模板：把当前指标、图表、默认参数、适用场景冻结成不可变快照。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"场景模板 {entry_id} 不存在或已归档"
        missing = [
            field for field in SNAPSHOT_FIELDS
            if entry.get(field) is None or entry.get(field) == "" or entry.get(field) == []
        ]
        if missing:
            return None, f"发布前需补全字段：{'、'.join(missing)}"
        if entry.get("status") == STATUS_ORDER[-1]:
            return None, "已归档模板不能再发布"

        snapshot = self._build_snapshot(entry)
        with self._write_lock:
            releases = self._releases(entry["模板编号"])
            # 双重检查：等锁期间可能已有其他请求完成了相同内容的发布。
            if releases and self._same_as_snapshot(entry, releases[-1]["snapshot"]):
                latest = releases[-1]
                entry["status"] = "已发布"
                entry["pending"] = False
                return latest, f"模板内容与冻结版本 {latest['version']} 一致，已复用该快照"
            version_no = len(releases) + 1
            release = {
                "id": self._next_id(RELEASE_MODULE),
                "模板编号": entry["模板编号"],
                "模板名称": entry.get("模板名称"),
                "version": f"v{version_no}",
                "frozen_at": datetime.now(timezone.utc).isoformat(),
                "snapshot": snapshot,
            }
            store.rows(RELEASE_MODULE).append(release)
        entry["status"] = "已发布"
        entry["pending"] = False
        entry["abnormal"] = False
        return release, f"已发布并冻结指标图表配置快照 {release['version']}"

    def list_releases(self, template_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(RELEASE_MODULE)
        if template_code:
            rows = [row for row in rows if row.get("模板编号") == template_code]
        return [copy.deepcopy(row) for row in rows]

    def get_release(self, release_id: int) -> dict[str, Any] | None:
        row = store.find(RELEASE_MODULE, release_id)
        return copy.deepcopy(row) if row else None

    # ---------------------------------------------------------------- 导出包
    def export_package(self, release_id: int) -> tuple[dict[str, Any] | None, str]:
        """为指定冻结版本生成（或复用）导出包记录，包内容随版本不可变。"""
        release = store.find(RELEASE_MODULE, release_id)
        if release is None:
            return None, f"冻结版本 {release_id} 不存在，无法导出"
        with self._write_lock:
            packages = store.rows(PACKAGE_MODULE)
            # 同一冻结版本只生成一次：重复导出拿到同一份包，内容天然一致。
            for pkg in packages:
                if pkg.get("release_id") == release_id:
                    return copy.deepcopy(pkg), "导出包已存在，复用首次冻结生成的包"

            manifest = self._build_manifest(release)
            package = {
                "id": self._next_id(PACKAGE_MODULE),
                "release_id": release["id"],
                "模板编号": release["模板编号"],
                "version": release["version"],
                "文件名": f"{release['模板编号']}-{release['version']}-scene-package.zip",
                "manifest": manifest,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            packages.append(package)
        return copy.deepcopy(package), "导出包已生成，包含指标图表快照与默认参数"

    def package_archive(self, package_id: int) -> tuple[bytes, str] | None:
        """把导出包打成确定性 zip：同一冻结版本每次下载字节完全相同。"""
        package = store.find(PACKAGE_MODULE, package_id)
        if package is None:
            return None
        release = store.find(RELEASE_MODULE, int(package["release_id"]))
        if release is None:
            return None
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            manifest_bytes = _json_dumps(package["manifest"]).encode("utf-8")
            snapshot_bytes = _json_dumps(release["snapshot"]).encode("utf-8")
            for name, data in (
                ("manifest.json", manifest_bytes),
                ("snapshot/metric-chart-snapshot.json", snapshot_bytes),
            ):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
        return buffer.getvalue(), str(package["文件名"])

    def list_packages(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(row) for row in store.rows(PACKAGE_MODULE)]

    # ---------------------------------------------------------------- 实例化
    def instantiate(
        self, release_id: int | None = None, *, template_code: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        """分析入口实例化：只读取冻结版本里的实例化参数。

        两个请求同时开始时，按模板编号串行化，锁内固定选取该模板的首个冻结
        版本，因此并发请求拿到的是同一套实例化参数（首个冻结版本为准）。
        """
        lock = self._lock_for(template_code or f"release-{release_id}")
        with lock:
            release = self._resolve_release(release_id, template_code)
            if release is None:
                hint = template_code or release_id
                return None, f"找不到 {hint} 的冻结版本，请先发布场景模板"
            snapshot = release["snapshot"]
            with self._write_lock:
                rows = store.rows(INSTANCE_MODULE)
                instance = {
                    "id": self._next_id(INSTANCE_MODULE),
                    "实例编号": f"SINS-{len(rows) + 1:04d}",
                    "模板编号": release["模板编号"],
                    "release_id": release["id"],
                    "冻结版本": release["version"],
                    "适用场景": copy.deepcopy(snapshot["适用场景"]),
                    "预设指标": copy.deepcopy(snapshot["预设指标"]),
                    "推荐图表": copy.deepcopy(snapshot["推荐图表"]),
                    # 消费侧分析入口只读这里，不回查模板工作副本。
                    "实例化参数": copy.deepcopy(snapshot["默认参数"]),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                rows.append(instance)
            return copy.deepcopy(instance), f"已按冻结版本 {release['version']} 实例化"

    def list_instances(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(row) for row in store.rows(INSTANCE_MODULE)]

    # ------------------------------------------------------------------ 辅助
    def _resolve_release(
        self, release_id: int | None, template_code: str | None
    ) -> dict[str, Any] | None:
        if release_id is not None:
            return store.find(RELEASE_MODULE, release_id)
        if not template_code:
            return None
        releases = self._releases(template_code)
        # 首个冻结版本：并发实例化时所有请求统一落到这里。
        return releases[0] if releases else None

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._instantiate_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._instantiate_locks[key] = lock
            return lock

    def _releases(self, template_code: str) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows(RELEASE_MODULE)
            if row.get("模板编号") == template_code
        ]

    def _build_snapshot(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "快照类型": "指标图表配置快照",
            "模板编号": entry["模板编号"],
            "适用场景": copy.deepcopy(entry["适用场景"]),
            "预设指标": copy.deepcopy(entry["预设指标"]),
            "推荐图表": copy.deepcopy(entry["推荐图表"]),
            "默认参数": copy.deepcopy(entry["默认参数"]),
        }

    def _same_as_snapshot(self, entry: dict[str, Any], snapshot: dict[str, Any]) -> bool:
        return all(entry.get(field) == snapshot.get(field) for field in SNAPSHOT_FIELDS)

    def _build_manifest(self, release: dict[str, Any]) -> dict[str, Any]:
        snapshot = release["snapshot"]
        return {
            "module": "scene",
            "模板编号": release["模板编号"],
            "模板名称": release.get("模板名称"),
            "冻结版本ID": release["id"],
            "冻结版本": release["version"],
            "冻结时间": release["frozen_at"],
            "适用场景": copy.deepcopy(snapshot["适用场景"]),
            "指标图表配置快照": copy.deepcopy(snapshot),
            "默认参数": copy.deepcopy(snapshot["默认参数"]),
            "快照文件": "snapshot/metric-chart-snapshot.json",
            "再次实例化": "POST /api/scene/instantiations，body 中携带 release_id=冻结版本ID",
        }

    def _with_summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        result = dict(entry)
        releases = self._releases(str(entry.get("模板编号", "")))
        result["冻结版本数"] = len(releases)
        result["当前冻结版本"] = releases[-1]["version"] if releases else None
        return result

    @staticmethod
    def _next_id(module: str) -> int:
        rows = store.rows(module)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1
