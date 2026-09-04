def test_texts_sets_have_expected_sizes(texts):
    assert len(texts.sets["A"]) == 10
    assert len(texts.sets["B"]) == 4
    assert len(texts.sets["V"]) == 3


def test_texts_stable_blocks_present(texts):
    assert "10:00" in texts.weekday_arrangements
    assert "керівнику" in texts.contacts


def test_texts_variant_ids_unique_per_set(texts):
    for variants in texts.sets.values():
        ids = [v.id for v in variants]
        assert len(ids) == len(set(ids))


def test_thresholds_loaded(thresholds):
    assert thresholds.min_alerts_count == 2
    assert thresholds.min_total_duration_minutes == 120
    assert thresholds.ballistic_threshold_multiplier == 0.5
    assert thresholds.night_window_start.hour == 22
    assert thresholds.night_window_end.hour == 7
    assert "ballistic" in thresholds.threat_type_keywords
