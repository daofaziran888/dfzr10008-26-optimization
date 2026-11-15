"""场景模板「发布冻结 → 导出包 → 实例化」验收脚本。

运行：.venv/bin/python -m scripts.verify_scene_snapshot
仅依赖标准库与本项目代码，每个进程从种子数据重新开始，可重复执行。
"""
from __future__ import annotations

import io
import json
import threading
import zipfile

from app.services.scene import SceneService
from app.store import store

TEMPLATE_ID = 2  # 种子数据中的 SCEN-0002


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label} 不符合预期：实际={actual!r}，期望={expected!r}")
    print(f"  ✓ {label}")


def main() -> None:
    svc = SceneService()
    entry = store.find("scene", TEMPLATE_ID)
    assert entry is not None and entry["模板编号"] == "SCEN-0002"

    print("1) 发布 SCN-0002，冻结指标图表配置快照")
    release, message = svc.publish_entry(TEMPLATE_ID)
    assert release is not None, message
    snapshot = release["snapshot"]
    assert_equal(release["version"], "v1", "首个冻结版本号")
    assert_equal(snapshot["预设指标"], ["GMV", "订单数", "复购率"], "预设指标冻结")
    assert_equal(snapshot["推荐图表"], ["折线图", "门店排行"], "推荐图表冻结")
    assert_equal(
        snapshot["默认参数"],
        {"时间范围": "最近30天", "区域": "华南区域"},
        "默认参数冻结（最近30天/华南区域）",
    )
    assert "适用场景" in snapshot and snapshot["适用场景"], "适用场景随快照携带"

    print("2) 导出包：重复导出内容一致，且包含快照与默认参数")
    pkg1, _ = svc.export_package(release["id"])
    pkg2, _ = svc.export_package(release["id"])
    assert pkg1 is not None and pkg2 is not None
    assert_equal(pkg1["id"], pkg2["id"], "同一冻结版本复用同一份导出包")
    archive1, _ = svc.package_archive(pkg1["id"])
    archive2, _ = svc.package_archive(pkg1["id"])
    assert_equal(archive1, archive2, "重复导出 zip 字节完全一致")
    with zipfile.ZipFile(io.BytesIO(archive1)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names and "snapshot/metric-chart-snapshot.json" in names
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        zipped_snapshot = json.loads(
            zf.read("snapshot/metric-chart-snapshot.json").decode("utf-8")
        )
    assert_equal(manifest["默认参数"], snapshot["默认参数"], "包内 manifest 携带默认参数")
    assert_equal(manifest["适用场景"], snapshot["适用场景"], "包内 manifest 携带适用场景")
    assert_equal(zipped_snapshot, snapshot, "包内快照与冻结快照一致")
    assert manifest["冻结版本ID"] == release["id"], "包支持凭 release_id 再次实例化"

    print("3) 分析入口实例化：只读取冻结的实例化参数")
    inst1, msg1 = svc.instantiate(template_code="SCEN-0002")
    assert inst1 is not None, msg1
    assert_equal(inst1["冻结版本"], "v1", "实例绑定首个冻结版本")
    assert_equal(inst1["实例化参数"]["区域"], "华南区域", "实例化默认区域为华南")

    print("4) 模板默认区域改为华东：只能产生新版本，旧实例保持华南")
    updated, update_msg = svc.update_entry(
        TEMPLATE_ID, {"默认参数": {"时间范围": "最近30天", "区域": "华东区域"}}
    )
    assert updated is not None, update_msg
    assert_equal(updated["status"], "草稿", "改动后模板回到草稿，等待重新发布")
    release2, message2 = svc.publish_entry(TEMPLATE_ID)
    assert release2 is not None, message2
    assert_equal(release2["version"], "v2", "重新发布产生新版本 v2")
    assert_equal(release2["snapshot"]["默认参数"]["区域"], "华东区域", "v2 冻结华东")
    old = store.find("scene_instance", inst1["id"])
    assert old is not None
    assert_equal(old["实例化参数"]["区域"], "华南区域", "旧实例仍使用华南，不随模板漂移")
    v1_pkg_again, _ = svc.export_package(release["id"])
    assert v1_pkg_again is not None
    assert_equal(
        svc.package_archive(v1_pkg_again["id"])[0], archive1, "v1 导出包内容保持不变"
    )

    print("5) 两个实例化同时开始：以首个冻结版本 v1 为准，结果一致")
    outcomes: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        inst, _ = svc.instantiate(template_code="SCEN-0002")
        assert inst is not None
        outcomes.append(
            (str(inst["冻结版本"]), json.dumps(inst["实例化参数"], sort_keys=True, ensure_ascii=False))
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert_equal(len(outcomes), 2, "两个并发请求都完成实例化")
    assert_equal(len(set(outcomes)), 1, "两个并发实例化结果完全一致")
    assert_equal(outcomes[0][0], "v1", "并发实例化统一落到首个冻结版本")
    assert "华南区域" in outcomes[0][1], "并发实例化参数仍是华南"

    print("6) 显式指定 v2 才能实例化华东参数")
    inst_v2, msg_v2 = svc.instantiate(release2["id"])
    assert inst_v2 is not None, msg_v2
    assert_equal(inst_v2["冻结版本"], "v2", "显式实例绑定 v2")
    assert_equal(inst_v2["实例化参数"]["区域"], "华东区域", "v2 实例化参数为华东")

    print("\n全部验收通过：发布冻结、确定性导出包、实例化只读冻结参数、并发以首个冻结版本为准。")


if __name__ == "__main__":
    main()
