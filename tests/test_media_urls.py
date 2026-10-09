from metricool_sync_posts.media.urls import (
    dropbox_direct_url,
    dropbox_download_candidates,
    extract_drive_file_id,
    google_drive_direct_url,
)


def test_dropbox_dl_param():
    url = "https://www.dropbox.com/s/abc/file.mov?dl=0"
    out = dropbox_direct_url(url)
    assert "dl=1" in out
    assert "raw=1" in out


def test_dropbox_scl_keeps_rlkey():
    url = "https://www.dropbox.com/scl/fi/abc123/video.mp4?rlkey=secret&dl=0"
    out = dropbox_direct_url(url)
    assert "rlkey=secret" in out
    assert "dl=1" in out
    assert "raw=1" in out


def test_dropbox_candidates_include_usercontent_host():
    url = "https://www.dropbox.com/s/abc/file.mov?dl=0"
    cands = dropbox_download_candidates(url)
    assert cands[0] == dropbox_direct_url(url)
    assert any("dl.dropboxusercontent.com" in c for c in cands)
    assert any("dl=1" in c and "raw=1" not in c for c in cands) or len(cands) >= 2


def test_drive_direct():
    url = "https://drive.google.com/file/d/FILEID/view"
    assert "export=download" in google_drive_direct_url(url)
    assert extract_drive_file_id(url) == "FILEID"
