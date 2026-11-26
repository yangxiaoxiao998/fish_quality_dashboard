from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.models import Fish
from app.services.path_service import to_data_relative


def upsert_fish_basic(
    db: Session,
    *,
    fish_id: str,
    video_id: str,
    track_id: int | None,
    bbox: tuple[float, float, float, float] | None,
    original_image_path: str | None,
    confidence: float | None = None,
    mask_image_path: str | None = None,
) -> None:
    if original_image_path is not None:
        original_image_path = to_data_relative(original_image_path)

    if mask_image_path is not None:
        mask_image_path = to_data_relative(mask_image_path)

    obj = db.get(Fish, fish_id)
    is_new = obj is None

    if obj is None:
        obj = Fish(
            fish_id=fish_id,
            video_id=video_id,
            track_id=track_id,
            status="processing",
            original_image_path=original_image_path,
        )
        db.add(obj)

    if track_id is not None:
        obj.track_id = track_id

    if bbox is not None:
        obj.bbox_x1, obj.bbox_y1, obj.bbox_x2, obj.bbox_y2 = bbox

    if original_image_path is not None:
        obj.original_image_path = original_image_path

    if mask_image_path is not None and hasattr(obj, "mask_image_path"):
        obj.mask_image_path = mask_image_path

    if confidence is not None:
        obj.confidence = confidence

    db.commit()

    if is_new:
        print(
            f"[DB] create fish: fish_id={fish_id!r} video_id={video_id!r} "
            f"track_id={track_id!r} original={original_image_path!r} committed=True"
        )
    else:
        print(
            f"[DB] update fish basic: fish_id={fish_id!r} "
            f"track_id={track_id!r} committed=True"
        )


def update_fish_corrected_image(
    db: Session,
    *,
    fish_id: str,
    corrected_image_path: str,
) -> None:
    corrected_image_path_rel = to_data_relative(corrected_image_path)
    if corrected_image_path_rel is None:
        print(
            f"[DB] update fish corrected image: invalid path, fish_id={fish_id!r} "
            f"raw={corrected_image_path!r}"
        )
        return

    obj = db.get(Fish, fish_id)
    if obj is None:
        print(f"[DB] update fish corrected image: fish not found, fish_id={fish_id!r}")
        return

    obj.corrected_image_path = corrected_image_path_rel
    db.commit()

    print(
        f"[DB] update fish corrected image: fish_id={fish_id!r} "
        f"corrected={corrected_image_path_rel!r} committed=True"
    )


def update_fish_five_dimensions(
    db: Session,
    *,
    fish_id: str,
    body_length: float | None,
    body_height: float | None,
    body_weight: float | None,
    condition_factor: float | None,
    surface_damage: str | None,
    status: str,
    body_length_without_tail: float | None = None,
    surface_damage_ratio: float | None = None,
    wound_mask_path: str | None = None,
    mask_image_path: str | None = None,
    error_msg: str | None = None,
) -> None:
    obj = db.get(Fish, fish_id)
    if obj is None:
        print(
            f"[DB] update fish five dimensions: fish not found, fish_id={fish_id!r}"
        )
        return

    obj.body_length = body_length
    obj.body_length_without_tail = body_length_without_tail
    obj.body_height = body_height
    obj.body_weight = body_weight
    # Keep legacy columns synchronized during the compatibility window while
    # all readers use the new unit-explicit columns.
    obj.body_length_cm = body_length
    obj.body_length_no_tail_cm = body_length_without_tail
    obj.body_height_cm = body_height
    obj.pred_weight_g = body_weight
    obj.condition_factor = condition_factor
    obj.surface_damage = surface_damage

    if hasattr(obj, "surface_damage_ratio"):
        obj.surface_damage_ratio = surface_damage_ratio

    if wound_mask_path is not None and hasattr(obj, "wound_mask_path"):
        obj.wound_mask_path = to_data_relative(wound_mask_path)

    if mask_image_path is not None and hasattr(obj, "mask_image_path"):
        obj.mask_image_path = to_data_relative(mask_image_path)

    obj.status = status
    obj.error_msg = error_msg

    db.commit()

    print(
        f"[DB] update fish five dimensions: fish_id={fish_id!r} status={status!r} "
        f"length={body_length} length_without_tail={body_length_without_tail} "
        f"height={body_height} weight={body_weight} "
        f"condition={condition_factor} damage={surface_damage!r} "
        f"damage_ratio={surface_damage_ratio} committed=True"
    )


def list_fish_by_video(
    db: Session,
    *,
    video_id: str,
    limit: int = 200,
) -> list[Fish]:
    stmt = (
        select(Fish)
        .where(Fish.video_id == video_id)
        .order_by(Fish.created_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def get_fish(
    db: Session,
    *,
    fish_id: str,
) -> Fish | None:
    return db.get(Fish, fish_id)


__all__ = [
    "upsert_fish_basic",
    "update_fish_corrected_image",
    "update_fish_five_dimensions",
    "list_fish_by_video",
    "get_fish",
]
