from core.youtube.hq_meta import build_hq_downloads


def test_muxed_beats_adaptive():
    player = {
        "streamingData": {
            "adaptiveFormats": [
                {
                    "mimeType": "video/webm; codecs=vp9",
                    "url": "http://a/1080.webm",
                    "width": 1920,
                    "height": 1080,
                    "bitrate": 5000000,
                },
                {
                    "mimeType": "audio/webm; codecs=opus",
                    "url": "http://a/audio.webm",
                    "bitrate": 128000,
                },
            ],
            "formats": [
                {
                    "mimeType": "video/mp4; codecs=avc1",
                    "url": "http://a/720.mp4",
                    "width": 1280,
                    "height": 720,
                    "bitrate": 2000000,
                },
            ],
        }
    }
    hq = build_hq_downloads(player)
    assert hq["hq_best_url"] == "http://a/720.mp4"
    assert hq["playback_best_url"] == "http://a/720.mp4"
    assert hq["has_audio"] is True
    assert hq["hq_best_source"] == "progressive"