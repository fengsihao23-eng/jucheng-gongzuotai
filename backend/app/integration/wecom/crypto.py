"""企业微信回调加解密（WXBizMsgCrypt 协议的 Python 实现）。

签名：sha1(sorted([token, timestamp, nonce, encrypt]))
密文：AES-256-CBC(key=Base64Decode(EncodingAESKey+"="), iv=key[:16])，PKCS#7(块 32)
明文：random(16B) + msg_len(4B 网络序) + msg + receive_id(CorpID)
"""
import base64
import hashlib
import os
import socket
import struct
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES


class WeComCryptoError(Exception):
    pass


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        if len(encoding_aes_key) != 43:
            raise WeComCryptoError("EncodingAESKey 长度必须为 43")
        self.token = token
        self.key = base64.b64decode(encoding_aes_key + "=")
        self.receive_id = receive_id

    def signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        return hashlib.sha1("".join(sorted([self.token, timestamp, nonce, encrypt])).encode()).hexdigest()

    def _check(self, sig: str, timestamp: str, nonce: str, encrypt: str) -> None:
        if self.signature(timestamp, nonce, encrypt) != sig:
            raise WeComCryptoError("签名校验失败")

    def decrypt(self, encrypt: str) -> str:
        raw = AES.new(self.key, AES.MODE_CBC, self.key[:16]).decrypt(base64.b64decode(encrypt))
        pad = raw[-1]
        if pad < 1 or pad > 32:
            raise WeComCryptoError("填充非法")
        content = raw[16:-pad]
        msg_len = socket.ntohl(struct.unpack("I", content[:4])[0])
        msg, rid = content[4:4 + msg_len], content[4 + msg_len:].decode()
        if rid != self.receive_id:
            raise WeComCryptoError("ReceiveId 不匹配")
        return msg.decode()

    def encrypt(self, msg: str) -> str:
        body = msg.encode()
        text = os.urandom(16) + struct.pack("I", socket.htonl(len(body))) + body + self.receive_id.encode()
        pad = 32 - len(text) % 32
        text += bytes([pad]) * pad
        return base64.b64encode(AES.new(self.key, AES.MODE_CBC, self.key[:16]).encrypt(text)).decode()

    # GET 校验 URL：返回解密后的 echostr 明文
    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        self._check(msg_signature, timestamp, nonce, echostr)
        return self.decrypt(echostr)

    # POST 事件：返回解密后的 XML 明文
    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, post_xml: str) -> str:
        encrypt = ET.fromstring(post_xml).findtext("Encrypt") or ""
        self._check(msg_signature, timestamp, nonce, encrypt)
        return self.decrypt(encrypt)

    def encrypt_msg(self, msg: str, timestamp: str, nonce: str) -> str:
        enc = self.encrypt(msg)
        sig = self.signature(timestamp, nonce, enc)
        return (f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt><MsgSignature><![CDATA[{sig}]]></MsgSignature>"
                f"<TimeStamp>{timestamp}</TimeStamp><Nonce><![CDATA[{nonce}]]></Nonce></xml>")


def parse_approval_event(xml_text: str) -> dict | None:
    """解析审批状态变化事件（Event=sys_approval_change）。非审批事件返回 None。"""
    root = ET.fromstring(xml_text)
    if root.findtext("Event") != "sys_approval_change":
        return None
    info = root.find("ApprovalInfo")
    if info is None:
        return None
    return {
        "sp_no": info.findtext("SpNo"),
        "sp_name": info.findtext("SpName"),
        "sp_status": int(info.findtext("SpStatus") or 0),
        "template_id": info.findtext("TemplateId"),
        "apply_time": int(info.findtext("ApplyTime") or 0),
        "applicant_userid": info.findtext("Applyer/UserId"),
    }


def parse_contact_event(xml_text: str) -> dict | None:
    """解析通讯录变更事件（Event=change_contact）：成员、部门、标签的增删改。"""
    root = ET.fromstring(xml_text)
    if root.findtext("Event") != "change_contact":
        return None
    return {
        "change_type": root.findtext("ChangeType") or "",
        "user_id": root.findtext("UserID") or "",
        "department_id": root.findtext("Id") or root.findtext("DepartmentId") or "",
        "name": root.findtext("Name") or "",
    }


def parse_member_message(xml_text: str) -> dict | None:
    """解析成员发给应用的普通消息（text/image/voice/video/location/link，以及客户端可能下发的 file）和菜单点击事件。

    审批、通讯录等系统事件返回 None，交给各自的解析函数。
    """
    root = ET.fromstring(xml_text)
    msg_type = root.findtext("MsgType") or ""
    base = {"msg_type": msg_type, "from_user": root.findtext("FromUserName") or "", "corp_id": root.findtext("ToUserName") or "",
            "agent_id": root.findtext("AgentID") or "", "create_time": int(root.findtext("CreateTime") or 0)}
    if msg_type == "event":
        event = (root.findtext("Event") or "").lower()
        if event not in ("click", "view"):
            return None
        return {**base, "event": event, "key": root.findtext("EventKey") or "",
                "msg_id": f"evt-{base['from_user']}-{base['create_time']}-{root.findtext('EventKey') or ''}"}
    if msg_type not in ("text", "image", "voice", "video", "location", "link", "file"):
        return None
    return {**base, "msg_id": root.findtext("MsgId") or f"{base['from_user']}-{base['create_time']}",
            "content": root.findtext("Content") or "", "media_id": root.findtext("MediaId") or "",
            "file_name": root.findtext("FileName") or root.findtext("Title") or "",
            "pic_url": root.findtext("PicUrl") or "", "url": root.findtext("Url") or ""}
