from metricool_sync_posts.media.final_file import FinalFileKind, classify_final_file


def test_classify_youtube():
    ref = classify_final_file("https://youtu.be/abc123")
    assert ref.kind == FinalFileKind.YOUTUBE


def test_classify_drive_folder():
    ref = classify_final_file(
        "https://drive.google.com/drive/folders/1abcXYZ?usp=sharing"
    )
    assert ref.kind == FinalFileKind.GOOGLE_DRIVE_FOLDER
    assert ref.drive_folder_id == "1abcXYZ"


def test_classify_drive_file():
    ref = classify_final_file("https://drive.google.com/file/d/FILEID/view")
    assert ref.kind == FinalFileKind.GOOGLE_DRIVE_FILE
    assert ref.drive_file_id == "FILEID"


def test_classify_plain_text_and_schemeless_hosts_are_invalid():
    title = classify_final_file("Pulso Cap. 2 — Poncho de los Ríos")
    assert title.kind == FinalFileKind.INVALID
    assert title.raw_url == "Pulso Cap. 2 — Poncho de los Ríos"

    dropbox = classify_final_file("www.dropbox.com/s/abc/file.mp4?dl=0")
    assert dropbox.kind == FinalFileKind.INVALID

    drive = classify_final_file("drive.google.com/file/d/FILEID/view")
    assert drive.kind == FinalFileKind.INVALID

    ftp = classify_final_file("ftp://files.example/clip.mp4")
    assert ftp.kind == FinalFileKind.INVALID

    bare = classify_final_file("https://")
    assert bare.kind == FinalFileKind.INVALID


def test_classify_empty_stays_empty_and_http_urls_stay_usable():
    assert classify_final_file(None).kind == FinalFileKind.EMPTY
    assert classify_final_file("").kind == FinalFileKind.EMPTY
    assert classify_final_file("   ").kind == FinalFileKind.EMPTY
    assert classify_final_file("  https://youtu.be/abc123  ").kind == FinalFileKind.YOUTUBE
    other = classify_final_file("HTTP://cdn.example/video.mp4")
    assert other.kind == FinalFileKind.OTHER_URL
