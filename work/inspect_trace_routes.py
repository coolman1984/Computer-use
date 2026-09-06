from __future__ import annotations

import collections
import json
import re
import urllib.parse
import zipfile
from pathlib import Path
from xml.etree import ElementTree

trace = Path("data/recordings/rec_01M1V5H13X861HV7/trace/trace.zip")
with zipfile.ZipFile(trace) as archive:
    members = set(archive.namelist())
    output = []
    for line in archive.read("trace.network").decode("utf-8").splitlines():
        event = json.loads(line)
        snapshot = event.get("snapshot") or {}
        request = snapshot.get("request") or {}
        if "XExportImport" not in request.get("url", ""):
            continue
        post = request.get("postData") or {}
        request_member = f"resources/{post.get('_sha1', '')}"
        response = snapshot.get("response") or {}
        response_member = f"resources/{(response.get('content') or {}).get('_sha1', '')}"
        body = archive.read(request_member) if request_member in members else b""
        response_body = archive.read(response_member) if response_member in members else b""
        tags: collections.Counter[str] = collections.Counter()
        attribute_names: set[str] = set()
        try:
            root = ElementTree.fromstring(body)
            for element in root.iter():
                tags[element.tag.split("}")[-1]] += 1
                attribute_names.update(element.attrib)
        except ElementTree.ParseError:
            pass
        decoded = body.decode("utf-8", "replace")
        records = decoded.split("\x1e")
        variables = []
        datasets = []
        xml_tags: collections.Counter[str] = collections.Counter()
        xml_datasets = []
        technical_parameters = []
        try:
            root = ElementTree.fromstring(body)
            for element in root.iter():
                tag = element.tag.split("}")[-1]
                xml_tags[tag] += 1
                identifier = element.attrib.get("id") or element.attrib.get("name") or ""
                if tag.lower() == "dataset" and identifier:
                    xml_datasets.append(identifier)
                if identifier and any(word in identifier.lower() for word in ("service", "method", "program", "menu", "transaction")):
                    value = element.attrib.get("value") or (element.text or "")
                    technical_parameters.append({"name": identifier[:100], "value": value[:120]})
        except ElementTree.ParseError:
            pass
        for record in records:
            if record.startswith("Dataset:"):
                datasets.append(record.split("\x1f", 1)[0][len("Dataset:"):])
                continue
            if "=" in record and "\x1f" not in record:
                key, value = record.split("=", 1)
                variables.append({"name": key[:100], "value_bytes": len(value.encode("utf-8"))})
        routes = []
        pattern = rb'https?://[^"\'<> ]+|/mes4/common/export/[^"\'<> ]+'
        for raw in re.findall(pattern, response_body):
            value = raw.decode("utf-8", "ignore")
            parsed = urllib.parse.urlsplit(value)
            routes.append(
                f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if parsed.scheme
                else parsed.path
            )
        output.append(
            {
                "request_bytes": len(body),
                "request_prefix_hex": body[:32].hex(),
                "record_count": len(records),
                "variables": variables[:30],
                "dataset_names": datasets[:30],
                "xml_tags": tags.most_common(20),
                "attribute_names": sorted(attribute_names)[:40],
                "response_bytes": len(response_body),
                "response_export_routes": routes,
            }
        )
print(json.dumps(output, ensure_ascii=False, indent=2))

with zipfile.ZipFile(trace) as archive:
    members = set(archive.namelist())
    calls = []
    for line in archive.read("trace.network").decode("utf-8").splitlines():
        event = json.loads(line)
        snapshot = event.get("snapshot") or {}
        request = snapshot.get("request") or {}
        route = urllib.parse.urlsplit(request.get("url", "")).path
        if request.get("method") != "POST" or not route.endswith("/nexacro.do"):
            continue
        post = request.get("postData") or {}
        member = f"resources/{post.get('_sha1', '')}"
        body = archive.read(member) if member in members else b""
        decoded = body.decode("utf-8", "replace")
        records = decoded.split("\x1e")
        variables = []
        datasets = []
        xml_tags: collections.Counter[str] = collections.Counter()
        xml_datasets = []
        technical_parameters = []
        try:
            root = ElementTree.fromstring(body)
            for element in root.iter():
                tag = element.tag.split("}")[-1]
                xml_tags[tag] += 1
                identifier = element.attrib.get("id") or element.attrib.get("name") or ""
                if tag.lower() == "dataset" and identifier:
                    xml_datasets.append(identifier)
                if identifier and any(word in identifier.lower() for word in ("service", "method", "program", "menu", "transaction")):
                    value = element.attrib.get("value") or (element.text or "")
                    technical_parameters.append({"name": identifier[:100], "value": value[:120]})
        except ElementTree.ParseError:
            # Some Nexacro payloads contain control characters inside data
            # cells. Extract only structural identifiers without printing data.
            for raw_id, raw_value in re.findall(
                rb'<Parameter\s+id="([^"]+)">(.*?)</Parameter>', body, re.DOTALL
            ):
                identifier = raw_id.decode("utf-8", "replace")
                technical_parameters.append(
                    {
                        "name": identifier[:100],
                        "value": (
                            raw_value.decode("utf-8", "replace")[:120]
                            if any(word in identifier.lower() for word in ("service", "method", "program", "menu", "transaction"))
                            else f"[{len(raw_value)} bytes]"
                        ),
                    }
                )
            xml_datasets.extend(
                value.decode("utf-8", "replace")
                for value in re.findall(rb'<Dataset\s+id="([^"]+)"', body)
            )
        for record in records:
            if record.startswith("Dataset:"):
                datasets.append(record.split("\x1f", 1)[0][len("Dataset:"):])
            elif "=" in record and "\x1f" not in record:
                key, value = record.split("=", 1)
                item = {"name": key[:100], "value_bytes": len(value.encode("utf-8"))}
                if any(word in key.lower() for word in ("service", "method", "program", "menu", "transaction")):
                    item["technical_value"] = value[:120]
                variables.append(item)
        content = (snapshot.get("response") or {}).get("content") or {}
        calls.append(
            {
                "route": route,
                "request_bytes": len(body),
                "variables": variables[:40],
                "datasets": datasets[:30],
                "xml_tags": xml_tags.most_common(20),
                "xml_datasets": xml_datasets[:30],
                "technical_parameters": technical_parameters[:40],
                "response_bytes": content.get("size"),
            }
        )
print(json.dumps({"nexacro_calls": calls}, ensure_ascii=False, indent=2))
