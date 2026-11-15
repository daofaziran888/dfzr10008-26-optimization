"""场景化配置模板接口：模板维护、发布冻结、导出包下载与分析入口实例化。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.scene import SceneService

router = APIRouter(prefix="/api/scene", tags=["场景化配置模板"])

service = SceneService()

LIST_FIELDS = ["模板编号", "模板名称", "适用场景", "预设指标", "推荐图表", "默认参数", "模板描述", "模板状态"]
STATUSES = ["草稿", "已发布", "已下线", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、已发布、已下线、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤场景化配置模板列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/releases", response_model=list[dict])
def list_releases(
    template_code: str | None = Query(default=None, description="按模板编号过滤冻结版本"),
) -> list[dict]:
    """查看发布冻结版本；每个版本携带不可变的指标图表配置快照。"""
    return service.list_releases(template_code)


@router.get("/releases/{release_id}", response_model=dict)
def get_release(release_id: int) -> dict:
    """读取单个冻结版本及其指标图表配置快照。"""
    release = service.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail=f"冻结版本 {release_id} 不存在")
    return release


@router.post("/releases/{release_id}/export", response_model=ActionResult)
def export_release_package(release_id: int) -> ActionResult:
    """把一次发布结果打成场景模板包：快照、默认参数、适用场景随包携带。

    同一冻结版本重复导出复用同一份包，保证内容一致。
    """
    package, message = service.export_package(release_id)
    if package is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=package)


@router.get("/packages", response_model=list[dict])
def list_packages() -> list[dict]:
    """列出已生成的场景模板导出包。"""
    return service.list_packages()


@router.get("/packages/{package_id}/download")
def download_package(package_id: int) -> Response:
    """下载场景模板包（zip）；包内为冻结快照与默认参数，可再次实例化。"""
    result = service.package_archive(package_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"导出包 {package_id} 不存在")
    archive, filename = result
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/instantiations", response_model=ActionResult)
def create_instance(payload: EntryPayload) -> ActionResult:
    """跨模块消费侧分析入口实例化：只读取冻结版本的实例化参数。

    可传 release_id 指定冻结版本；也可只传模板编号，此时以该模板首个冻结
    版本为准。并发实例化在服务内串行选版本，同时开始的请求得到同一参数。
    """
    values = payload.values
    release_id = values.get("release_id")
    template_code = str(values.get("模板编号") or "").strip() or None
    if release_id is not None:
        try:
            release_id = int(release_id)
        except (TypeError, ValueError):
            return ActionResult(ok=False, message="release_id 必须是整数")
    instance, message = service.instantiate(release_id, template_code=template_code)
    if instance is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=instance)


@router.get("/instantiations", response_model=list[dict])
def list_instances() -> list[dict]:
    """查看分析入口实例化记录；实例化参数来自冻结快照，不随模板编辑漂移。"""
    return service.list_instances()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出场景化配置模板清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "scene", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条场景模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"场景模板 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条场景模板，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="场景模板已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改模板工作副本；已有冻结版本时只在重新发布后产生新版本，旧实例不受影响。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条场景模板执行发布模板、下线模板、归档模板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
