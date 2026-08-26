from transformers import AutoTokenizer, BertModel, BertTokenizer, RobertaModel, RobertaTokenizerFast
import os
from pathlib import Path


def _local_bert_path() -> str:
    project_root = Path(__file__).resolve().parents[5]
    path = project_root / "algorithms" / "pth" / "bert-base-uncased"
    return str(path) if (path / "config.json").exists() else ""

def get_tokenlizer(text_encoder_type):
    if not isinstance(text_encoder_type, str):
        # print("text_encoder_type is not a str")
        if hasattr(text_encoder_type, "text_encoder_type"):
            text_encoder_type = text_encoder_type.text_encoder_type
        elif text_encoder_type.get("text_encoder_type", False):
            text_encoder_type = text_encoder_type.get("text_encoder_type")
        elif os.path.isdir(text_encoder_type) and os.path.exists(text_encoder_type):
            pass
        else:
            raise ValueError(
                "Unknown type of text_encoder_type: {}".format(type(text_encoder_type))
            )
    print("final text_encoder_type: {}".format(text_encoder_type))

    local_path = _local_bert_path() if text_encoder_type == "bert-base-uncased" else ""
    tokenizer = AutoTokenizer.from_pretrained(local_path or text_encoder_type, local_files_only=bool(local_path))
    return tokenizer


def get_pretrained_language_model(text_encoder_type):
    if text_encoder_type == "bert-base-uncased" or (os.path.isdir(text_encoder_type) and os.path.exists(text_encoder_type)):
        local_path = _local_bert_path()
        return BertModel.from_pretrained(local_path or text_encoder_type, local_files_only=bool(local_path))
    if text_encoder_type == "roberta-base":
        return RobertaModel.from_pretrained(text_encoder_type)

    raise ValueError("Unknown text_encoder_type {}".format(text_encoder_type))
