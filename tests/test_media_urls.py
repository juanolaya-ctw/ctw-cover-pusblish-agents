from metricool_sync_posts.media.urls import dropbox_direct_url, google_drive_direct_url


def test_dropbox_dl_param():
    url = "https://www.dropbox.com/s/abc/file.mov?dl=0"
    assert "dl=1" in dropbox_direct_url(url)


def test_drive_direct():
    url = "https://drive.google.com/file/d/FILEID/view"
    assert "export=download" in google_drive_direct_url(url)
