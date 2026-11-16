"""Turning whatever the attendance software stores for a photo into image bytes.

Shared by `tools/export_photos.py` (bulk) and `profiles.py` (one user at a time,
on the server's request). Read-only: files are opened for reading, nothing else.
"""

import base64
import os
import re

# Where BioTime-style software usually keeps uploaded files, relative to a root.
SUBDIRS = ("", "files", "files/photo", "files/upload", "media", "attachment", "biotime/files", "files/biophoto")
MAX_BYTES = 5 * 1024 * 1024


def _kind(data):
	if data[:3] == b"\xff\xd8\xff":
		return "jpg"
	if data[:8] == b"\x89PNG\r\n\x1a\n":
		return "png"
	if data[:2] == b"BM":
		return "bmp"
	return None


def to_image(value, roots):
	"""(bytes, ext, source) from whatever the column holds: a blob, base64 text,
	a data: URI or a file path. (None, None, reason) when it cannot be found."""
	if value is None:
		return None, None, "empty"
	if isinstance(value, (bytes, bytearray, memoryview)):
		data = bytes(value)
		kind = _kind(data)
		return (data, kind, "blob") if kind else (None, None, "blob, not an image")
	text = str(value).strip()
	if not text:
		return None, None, "empty"
	if text.startswith("data:image"):
		text = text.split(",", 1)[-1]
	if len(text) > 200 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text[:400]):
		try:
			data = base64.b64decode(text)
			kind = _kind(data)
			if kind:
				return data, kind, "base64"
		except Exception:
			pass
	rel = text.lstrip("/\\").replace("\\", "/")
	candidates = [text] if os.path.isabs(text) else []
	for root in roots:
		for sub in SUBDIRS:
			candidates.append(os.path.join(root, sub, rel))
			candidates.append(os.path.join(root, sub, os.path.basename(rel)))
	for path in candidates:
		if os.path.isfile(path) and os.path.getsize(path) <= MAX_BYTES:
			with open(path, "rb") as fh:
				data = fh.read()
			kind = _kind(data)
			if kind:
				return data, kind, f"file {path}"
	return None, None, f"file not found: {text}"
