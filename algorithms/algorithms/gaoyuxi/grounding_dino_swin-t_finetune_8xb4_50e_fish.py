import os
import sys
from pathlib import Path

_base_ = './grounding_dino_swin-t_pretrain_obj365.py'

# 同 grounding_dino_swin-t_pretrain_obj365.py：mmengine.Config.fromfile 用 eval() 加载，
# ``__file__`` 不一定存在。先尝试，失败回落到 ``Path.cwd()``。
try:
    _THIS_FILE = Path(globals().get('__file__') or os.path.abspath('grounding_dino_swin-t_finetune_8xb4_50e_fish.py'))
except Exception:
    _THIS_FILE = Path.cwd()
_DEFAULT_REPO_ROOT = _THIS_FILE.resolve().parent
_REPO_ROOT = Path(os.environ.get('ALGORITHMS_REPO_ROOT', str(_DEFAULT_REPO_ROOT)))

if str(_REPO_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT.parent))

try:
    from config.paths import BERT_BASE_UNCASED_DIR  # type: ignore  # noqa: E402
except Exception:
    BERT_BASE_UNCASED_DIR = None

data_root = str(_REPO_ROOT / "mydataset_coco")
class_name = ('fish', )


def _resolve_local_bert_path() -> str:
    # 优先级 1: 环境变量 GDINO_BERT_PATH 显式覆盖
    env_path = os.environ.get('GDINO_BERT_PATH')
    if env_path and os.path.isdir(env_path):
        return env_path

    # 优先级 2: 来自 algorithms/config/paths.py 的 BERT_BASE_UNCASED_DIR
    # （通常是 algorithms/algorithms/gaoyuxi/bert-base-uncased，含 pytorch_model.bin）
    if BERT_BASE_UNCASED_DIR is not None and Path(BERT_BASE_UNCASED_DIR).is_dir():
        model_bin = Path(BERT_BASE_UNCASED_DIR) / 'pytorch_model.bin'
        if model_bin.is_file():
            return str(BERT_BASE_UNCASED_DIR)

    # 优先级 3: HuggingFace 缓存（仅在 pytorch_model.bin 存在时）
    cache_root = Path.home() / '.cache' / 'huggingface' / 'hub' / 'models--bert-base-uncased' / 'snapshots'
    if cache_root.is_dir():
        snapshot_dirs = sorted([path for path in cache_root.iterdir() if path.is_dir()])
        for snap in snapshot_dirs:
            if (snap / 'pytorch_model.bin').is_file():
                return str(snap)

    # 优先级 4: 算法根目录下的 bert-base-uncased
    fallback = _REPO_ROOT / "bert-base-uncased"
    if fallback.is_dir():
        return str(fallback)

    return str(BERT_BASE_UNCASED_DIR)


lang_model_name = _resolve_local_bert_path()
num_classes = len(class_name)
label_name = '_annotations.coco.json'
palette = [(220, 20, 60)]

metainfo = dict(classes=class_name, palette=palette)

model = dict(
    language_model=dict(name=lang_model_name),
    bbox_head=dict(num_classes=num_classes))

train_pipeline = [
    dict(type='LoadImageFromFile'),
    dict(type='LoadAnnotations', with_bbox=True),
    dict(type='RandomFlip', prob=0.5),
    dict(
        type='RandomChoice',
        transforms=[
            [
                dict(
                    type='RandomChoiceResize',
                    scales=[(480, 1333), (512, 1333), (544, 1333), (576, 1333),
                            (608, 1333), (640, 1333), (672, 1333), (704, 1333),
                            (736, 1333), (768, 1333), (800, 1333)],
                    keep_ratio=True)
            ],
            [
                dict(
                    type='RandomChoiceResize',
                    scales=[(400, 4200), (500, 4200), (600, 4200)],
                    keep_ratio=True),
                dict(
                    type='RandomCrop',
                    crop_type='absolute_range',
                    crop_size=(384, 600),
                    allow_negative_crop=True),
                dict(
                    type='RandomChoiceResize',
                    scales=[(480, 1333), (512, 1333), (544, 1333), (576, 1333),
                            (608, 1333), (640, 1333), (672, 1333), (704, 1333),
                            (736, 1333), (768, 1333), (800, 1333)],
                    keep_ratio=True)
            ]
        ]),
    dict(type='FilterAnnotations', min_gt_bbox_wh=(1e-2, 1e-2)),
    dict(
        type='PackDetInputs',
        meta_keys=('img_id', 'img_path', 'ori_shape', 'img_shape',
                   'scale_factor', 'flip', 'flip_direction', 'text',
                   'custom_entities', 'tokens_positive', 'dataset_mode'))
]

test_pipeline = [
    dict(
        type='LoadImageFromFile',
        imdecode_backend='pillow'),
    dict(
        type='FixScaleResize',
        scale=(800, 1333),
        keep_ratio=True,
        backend='pillow'),
    dict(type='LoadAnnotations', with_bbox=True),
    dict(
        type='PackDetInputs',
        meta_keys=('img_id', 'img_path', 'ori_shape', 'img_shape',
                   'scale_factor', 'text', 'custom_entities',
                   'tokens_positive'))
]

train_dataloader = dict(
    _delete_=True,
    batch_size=1,
    num_workers=0,
    persistent_workers=False,
    sampler=dict(type='DefaultSampler', shuffle=True),
    batch_sampler=dict(type='AspectRatioBatchSampler'),
    dataset=dict(
        type='RepeatDataset',
        times=10,
        dataset=dict(
            type='CocoDataset',
            data_root=data_root,
            metainfo=metainfo,
            filter_cfg=dict(filter_empty_gt=False, min_size=32),
            pipeline=train_pipeline,
            return_classes=True,
            data_prefix=dict(img='train/'),
            ann_file='train/' + label_name)))

val_dataloader = dict(
    batch_size=1,
    num_workers=0,
    persistent_workers=False,
    dataset=dict(
        metainfo=metainfo,
        data_root=data_root,
        return_classes=True,
        ann_file='valid/' + label_name,
        data_prefix=dict(img='valid/'),
        pipeline=test_pipeline))
test_dataloader = val_dataloader

val_evaluator = dict(
    type='CocoMetric',
    ann_file=data_root + 'valid/' + label_name,
    metric='bbox',
    format_only=False)
test_evaluator = val_evaluator

optim_wrapper = dict(
    _delete_=True,
    type='OptimWrapper',
    optimizer=dict(type='AdamW', lr=0.0001, weight_decay=0.0001),
    clip_grad=dict(max_norm=0.1, norm_type=2),
    paramwise_cfg=dict(custom_keys={
        'absolute_pos_embed': dict(decay_mult=0.),
        'backbone': dict(lr_mult=0.1),
        'language_model': dict(lr_mult=0.0),
    }))

max_epochs = 20
param_scheduler = [
    dict(
        type='MultiStepLR',
        begin=0,
        end=max_epochs,
        by_epoch=True,
        milestones=[15],
        gamma=0.1)
]
train_cfg = dict(max_epochs=max_epochs, val_interval=1)

default_hooks = dict(checkpoint=dict(max_keep_ckpts=1, save_best='auto'))

load_from = 'https://download.openmmlab.com/mmdetection/v3.0/mm_grounding_dino/grounding_dino_swin-t_pretrain_obj365_goldg_grit9m_v3det/grounding_dino_swin-t_pretrain_obj365_goldg_grit9m_v3det_20231204_095047-b448804b.pth'  # noqa
# ===== Force local BERT path =====
local_bert_path = _resolve_local_bert_path()

try:
    lang_model_name = local_bert_path
except Exception:
    pass

try:
    model["language_model"]["name"] = local_bert_path
except Exception:
    pass

# ===== End force local BERT path =====

# ===== Remove invalid tokenizer_name from language_model =====
try:
    model["language_model"].pop("tokenizer_name", None)
except Exception:
    pass
# ===== End remove invalid tokenizer_name =====
