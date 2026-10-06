from app.modules.pipeline.vector_index import rrf_fuse


def _hit(chunk_id, score):
    return {"chunkId": chunk_id, "documentId": "1", "documentTitle": "t",
            "content": "c", "heading": None, "score": score}


def test_rrf_math():
    keyword = [_hit("a", 10.0), _hit("b", 5.0)]
    vector = [_hit("a", 0.9), _hit("b", 0.8)]
    fused = rrf_fuse(keyword, vector, 60)
    by_id = {h["chunkId"]: h for h in fused}
    # 两路都第一的 a：1/61 + 1/61；两路都第二的 b：1/62 + 1/62
    assert by_id["a"]["score"] > by_id["b"]["score"]
    assert abs(by_id["a"]["score"] - 2 / 61) < 1e-9
    assert abs(by_id["b"]["score"] - 2 / 62) < 1e-9
    assert by_id["a"]["bm25Score"] == 10.0
    assert by_id["a"]["vectorScore"] == 0.9


def test_single_channel_only():
    keyword = [_hit("a", 3.0), _hit("b", 1.0)]
    fused = rrf_fuse(keyword, [], 60)
    assert [h["chunkId"] for h in fused] == ["a", "b"]
    assert fused[0]["score"] == 1 / 61


def test_sorted_desc():
    keyword = [_hit("a", 1.0), _hit("b", 2.0)]
    vector = [_hit("b", 0.5), _hit("c", 0.9)]
    fused = rrf_fuse(keyword, vector, 60)
    scores = [h["score"] for h in fused]
    assert scores == sorted(scores, reverse=True)
