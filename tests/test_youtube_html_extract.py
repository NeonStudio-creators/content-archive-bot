from core.youtube.html_extract import extract_balanced_json, extract_player_response


def test_balanced_json_nested():
    html = 'var x = {"a": {"b": 1}, "c": 2};'
    pos = html.index("{")
    raw = extract_balanced_json(html, pos)
    assert raw == '{"a": {"b": 1}, "c": 2}'


def test_extract_player_response():
    payload = (
        'ytInitialPlayerResponse = {"videoDetails":{"title":"Test"},'
        '"streamingData":{"formats":[{"url":"http://x/1.mp4","mimeType":"video/mp4"}]},'
        '"playabilityStatus":{"status":"OK"}};'
    )
    data = extract_player_response(payload)
    assert data is not None
    assert data["videoDetails"]["title"] == "Test"