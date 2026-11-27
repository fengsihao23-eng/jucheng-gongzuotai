"""Accept existing JSON feedback requests or the same payload with multipart attachments."""
from collections import defaultdict
from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app.core.response import bad_request
from app.services.feedback_attachments import MAX_BYTES, MAX_FILES, Uploads, filename_of


@dataclass
class FeedbackPayload[Model: BaseModel]:
    body: Model
    uploads: Uploads


def _validate(model, raw):
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError([{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]) from None


def feedback_payload(model, *, group_count=None):
    async def parse(request: Request):
        media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type != "multipart/form-data":
            raw = await request.body()
            yield FeedbackPayload(_validate(model, raw or b"null"), Uploads())
            return

        try:
            form = await request.form(max_files=MAX_FILES, max_fields=1, max_part_size=2 * 1024 * 1024)
        except HTTPException as exc:
            raise bad_request(f"附件表单无效，每次最多上传 {MAX_FILES} 个文件") from exc
        try:
            raw = form.get("payload")
            if not isinstance(raw, str):
                raise bad_request("缺少反馈内容")
            body = _validate(model, raw)
            groups = defaultdict(list)
            for field, file in form.multi_items():
                if field == "payload":
                    continue
                prefix, separator, number = field.partition("-")
                if (prefix != "files" or not isinstance(file, UploadFile) or
                        (separator and (group_count is None or not number.isascii() or not number.isdecimal() or len(number) > 2))):
                    raise bad_request("附件字段无效")
                index = int(number) if separator else 0
                if group_count and index >= group_count(body):
                    raise bad_request("附件须对应本次提交的反馈记录")
                if file.size is not None and file.size > MAX_BYTES:
                    raise bad_request("单个附件不能超过 50 MB")
                groups[index].append((file, filename_of(file)))
            uploads = Uploads(groups)
            try:
                yield FeedbackPayload(body, uploads)
            finally:
                uploads.discard()
        finally:
            await form.close()

    return parse
