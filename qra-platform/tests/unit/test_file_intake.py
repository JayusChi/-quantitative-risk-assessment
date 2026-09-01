from __future__ import annotations

import io
import stat
import unittest
import zipfile

from PIL import Image
from pypdf import PdfWriter

from db_qra.conversion_adapter import conversion_dedupe_key
from db_qra.file_intake import (
    INTAKE_RULES_VERSION,
    MAX_JPEG_TRAILING_BYTES,
    detect_file_type,
    intake_files,
)


def zip_bytes(entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def ooxml_bytes(kind: str) -> bytes:
    part = "xl/workbook.xml" if kind == "XLSX" else "word/document.xml"
    return zip_bytes(
        [
            ("[Content_Types].xml", b"<Types/>") ,
            (part, b"<document/>") ,
        ]
    )


def image_bytes(format_name: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 3), (10, 20, 30)).save(output, format=format_name)
    return output.getvalue()


def pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class FileIntakeTest(unittest.TestCase):
    def test_supported_signatures_and_ooxml_parts(self) -> None:
        cases = [
            ("data.csv", b"id,value\nA,1\n", "CSV", "text/csv"),
            ("legacy.xls", bytes.fromhex("D0CF11E0A1B11AE1") + b"payload", "XLS", None),
            ("book.xlsx", ooxml_bytes("XLSX"), "XLSX", None),
            ("report.docx", ooxml_bytes("DOCX"), "DOCX", None),
            ("report.pdf", pdf_bytes(), "PDF", "application/pdf"),
            ("map.png", image_bytes("PNG"), "PNG", "image/png"),
            ("photo.jpg", image_bytes("JPEG"), "JPEG", "image/jpeg"),
        ]
        for file_name, content, expected_type, expected_mime in cases:
            with self.subTest(file_name=file_name):
                detected = detect_file_type(content, file_name=file_name)
                self.assertEqual(detected.type_id, expected_type)
                if expected_mime:
                    self.assertEqual(detected.media_type, expected_mime)

    def test_csv_encodings_structure_and_nul_detection(self) -> None:
        utf8_bom = b"\xef\xbb\xbf" + "编号,数值\nA,1\n".encode()
        gb18030 = "编号,数值\n甲,2\n".encode("gb18030")
        for content in (utf8_bom, gb18030):
            self.assertEqual(detect_file_type(content, file_name="data.csv").type_id, "CSV")
        for content in (b"a,b\n1\n", b"a\x00,b\n1,2\n", b"a^b\n1^2\n"):
            with self.subTest(content=content):
                result = intake_files([{"file_name": "bad.csv", "content": content}])
                self.assertEqual(result.sources[0]["security_status"], "QUARANTINED")
                self.assertEqual(result.issues[0].code, "INTAKE.INVALID_CSV")

    def test_csv_with_title_and_grouped_header_rows_is_accepted(self) -> None:
        content = (
            "高后果区管理表\n"
            "基础信息,,,管道信息,,\n"
            "序号,管道名称,起点,终点,长度(m),管径(mm)\n"
            "1,脱敏试点支线,0+000,10+938,10938,273\n"
        ).encode()
        batch = intake_files([{"file_name": "试点台账.csv", "content": content}])
        self.assertEqual(batch.ready_count, 1)
        self.assertEqual(batch.quarantined_count, 0)
        self.assertEqual(batch.sources[0]["security_status"], "READY_FOR_PARSE")

    def test_decodable_jpeg_small_trailing_metadata_is_accepted_silently(self) -> None:
        content = image_bytes("JPEG") + b"wechat-trailing-metadata"
        batch = intake_files(
            [{"file_name": "photo.jpg", "media_type": "image/jpeg", "content": content}]
        )
        self.assertEqual(batch.ready_count, 1)
        self.assertEqual(batch.quarantined_count, 0)
        self.assertEqual(batch.sources[0]["security_status"], "READY_FOR_PARSE")
        self.assertEqual(batch.issues, [])

    def test_truncated_or_excessively_appended_jpeg_is_still_quarantined(self) -> None:
        valid = image_bytes("JPEG")
        cases = {
            "truncated.jpg": valid[:-2],
            "oversized-tail.jpg": valid + b"x" * (MAX_JPEG_TRAILING_BYTES + 1),
        }
        for file_name, content in cases.items():
            with self.subTest(file_name=file_name):
                batch = intake_files([{"file_name": file_name, "content": content}])
                self.assertEqual(batch.ready_count, 0)
                self.assertEqual(batch.quarantined_count, 1)
                self.assertEqual(batch.issues[0].code, "INTAKE.INVALID_IMAGE")

    def test_type_mismatch_and_damaged_ooxml_are_quarantined(self) -> None:
        mismatch = intake_files(
            [{"file_name": "pretend.pdf", "content": b"id,value\nA,1\n"}]
        )
        self.assertEqual(mismatch.sources[0]["security_status"], "QUARANTINED")
        self.assertEqual(mismatch.issues[0].code, "INTAKE.TYPE_MISMATCH")

        damaged = intake_files(
            [{"file_name": "damaged.xlsx", "content": zip_bytes([("notes.txt", b"x")])}]
        )
        self.assertEqual(damaged.sources[0]["security_status"], "QUARANTINED")
        self.assertEqual(damaged.issues[0].code, "INTAKE.INVALID_OOXML")

    def test_zip_members_are_registered_with_archive_lineage(self) -> None:
        archive = zip_bytes(
            [
                ("folder/data.csv", b"id,value\nA,1\n"),
                ("folder/readme.txt", b"not supported"),
                ("nested.zip", zip_bytes([("inner.csv", b"id,value\nB,2\n")])),
            ]
        )
        batch = intake_files([{"file_name": "bundle.zip", "content": archive}])
        self.assertEqual(len(batch.sources), 4)
        archive_source = batch.sources[0]
        member = next(row for row in batch.sources if row["relative_path"] == "folder/data.csv")
        self.assertEqual(archive_source["security_status"], "VALIDATED")
        self.assertEqual(member["security_status"], "READY_FOR_PARSE")
        self.assertEqual(member["archive_name"], "bundle.zip")
        self.assertEqual(member["archive_member_path"], "folder/data.csv")
        self.assertEqual(
            {issue.code for issue in batch.issues},
            {"INTAKE.UNSUPPORTED_MEMBER", "INTAKE.NESTED_ARCHIVE"},
        )

    def test_unsafe_zip_variants_are_blocked_before_extraction(self) -> None:
        symlink = zipfile.ZipInfo("link.csv")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        variants = {
            "empty": zip_bytes([]),
            "traversal": zip_bytes([("../escape.csv", b"id,value\nA,1\n")]),
            "drive": zip_bytes([("C:/escape.csv", b"id,value\nA,1\n")]),
            "reserved": zip_bytes([("CON.csv", b"id,value\nA,1\n")]),
            "ads": zip_bytes([("folder:stream/data.csv", b"id,value\nA,1\n")]),
            "duplicate": zip_bytes(
                [("Data.csv", b"id,value\nA,1\n"), ("data.csv", b"id,value\nB,2\n")]
            ),
            "symlink": zip_bytes([(symlink, b"target.csv")]),
            "ratio": zip_bytes([("zeros.csv", b"0" * (2 * 1024 * 1024))]),
        }
        expected_codes = {
            "empty": "INTAKE.ZIP_NO_FILE_MEMBERS",
            "traversal": "INTAKE.ZIP_PATH_TRAVERSAL",
            "drive": "INTAKE.ZIP_PATH_TRAVERSAL",
            "reserved": "INTAKE.ZIP_INVALID_NAME",
            "ads": "INTAKE.ZIP_INVALID_NAME",
            "duplicate": "INTAKE.ZIP_DUPLICATE_PATH",
            "symlink": "INTAKE.ZIP_SYMLINK",
            "ratio": "INTAKE.ZIP_COMPRESSION_RATIO",
        }
        for name, archive in variants.items():
            with self.subTest(name=name):
                batch = intake_files([{"file_name": f"{name}.zip", "content": archive}])
                self.assertEqual(batch.sources[0]["security_status"], "QUARANTINED")
                self.assertEqual(batch.issues[0].code, expected_codes[name])

    def test_encrypted_member_flag_is_rejected(self) -> None:
        raw = bytearray(zip_bytes([("secret.csv", b"id,value\nA,1\n")]))
        local = raw.find(b"PK\x03\x04")
        central = raw.find(b"PK\x01\x02")
        raw[local + 6 : local + 8] = (1).to_bytes(2, "little")
        raw[central + 8 : central + 10] = (1).to_bytes(2, "little")
        batch = intake_files([{"file_name": "encrypted.zip", "content": bytes(raw)}])
        self.assertEqual(batch.sources[0]["security_status"], "QUARANTINED")
        self.assertEqual(batch.issues[0].code, "INTAKE.ZIP_ENCRYPTED")

    def test_archive_member_limit_applies_across_the_whole_task(self) -> None:
        first = zip_bytes(
            [(f"a/{index}.csv", f"id,value\nA,{index}\n".encode()) for index in range(101)]
        )
        second = zip_bytes(
            [(f"b/{index}.csv", f"id,value\nB,{index}\n".encode()) for index in range(101)]
        )
        batch = intake_files(
            [
                {"file_name": "first.zip", "content": first},
                {"file_name": "second.zip", "content": second},
            ]
        )
        second_archive = next(
            source for source in batch.sources if source["relative_path"] == "second.zip"
        )
        self.assertEqual(second_archive["security_status"], "QUARANTINED")
        self.assertIn("INTAKE.ZIP_MEMBER_LIMIT", {issue.code for issue in batch.issues})

    def test_duplicate_version_group_manifest_and_dedupe_are_deterministic(self) -> None:
        same = b"id,value\nA,1\n"
        files = [
            {"file_name": "a.csv", "content": same},
            {"file_name": "b.csv", "content": same},
        ]
        first = intake_files(files)
        second = intake_files(list(reversed(files)))
        self.assertEqual(first.file_manifest_sha256, second.file_manifest_sha256)
        self.assertEqual(
            [row["security_status"] for row in first.sources],
            ["READY_FOR_PARSE", "DUPLICATE"],
        )

        versions = intake_files(
            [
                {
                    "file_name": "v1.zip",
                    "content": zip_bytes([("资料.csv", b"id,value\nA,1\n")]),
                },
                {
                    "file_name": "v2.zip",
                    "content": zip_bytes([("资料.csv", b"id,value\nA,2\n")]),
                },
            ]
        )
        members = [row for row in versions.sources if row["archive_name"]]
        self.assertTrue(members[0]["version_group_id"])
        self.assertEqual(members[0]["version_group_id"], members[1]["version_group_id"])

        key = conversion_dedupe_key(first.sources, "mapping", "contract")
        changed = conversion_dedupe_key(
            first.sources,
            "mapping",
            "contract",
            intake_rules_version=INTAKE_RULES_VERSION + ".changed",
        )
        self.assertNotEqual(key, changed)


if __name__ == "__main__":
    unittest.main()
