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
