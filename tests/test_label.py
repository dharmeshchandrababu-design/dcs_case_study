"""Label step: model answers in slightly wrong formats are repaired or rejected, never stored broken."""
import label

GOOD = {"relevant": True, "theme": "price", "subcategory": "roaming_surcharge", "sentiment": "negative",
        "feedback_type": "complaint", "severity": "none", "network": "Ionity", "confidence": 0.8}


def test_valid_answer_passes():
    clean, reason = label.validate(GOOD)
    assert reason is None and clean["theme"] == "price" and clean["network_mentioned"] == "Ionity"


def test_theme_and_subcategory_in_one_field_is_repaired():
    clean, reason = label.validate({**GOOD, "theme": "price / roaming_surcharge", "subcategory": ""})
    assert reason is None and (clean["theme"], clean["subcategory"]) == ("price", "roaming_surcharge")


def test_capitals_and_spaces_are_repaired():
    clean, _ = label.validate({**GOOD, "theme": "Price", "subcategory": "Roaming Surcharge", "sentiment": "Negative"})
    assert (clean["theme"], clean["subcategory"], clean["sentiment"]) == ("price", "roaming_surcharge", "negative")


def test_missing_theme_taken_from_subcategory():
    clean, _ = label.validate({**GOOD, "theme": "", "subcategory": "roaming_surcharge"})
    assert clean["theme"] == "price"


def test_unknown_values_are_rejected():
    assert label.validate({**GOOD, "subcategory": "made_up"})[0] is None
    assert label.validate({**GOOD, "sentiment": "angry"})[0] is None
    assert label.validate({**GOOD, "severity": "huge"})[0] is None


def test_confidence_clipped_and_relevant_text_parsed():
    clean, _ = label.validate({**GOOD, "confidence": 7, "relevant": "yes", "network": "null"})
    assert clean["label_confidence"] == 1.0 and clean["relevant"] is True and clean["network_mentioned"] == "none"


def test_answer_shapes():
    assert label.answer_items({"labels": [GOOD]}) == [GOOD]
    assert label.answer_items([GOOD]) == [GOOD]
    assert label.answer_items(GOOD) == [GOOD]
    assert label.answer_items("not json") == []
