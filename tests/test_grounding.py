

def test_whitespace_is_not_fabrication():
    """Found live 2026-09-28: the model quoted a headline correctly but with
    one space where the source had two, and the quote was rejected.

    An ungrounded opinion has its veto discarded and its conviction reset to
    neutral, so a correct citation was silently overruled by formatting the
    model could not see. RSS feeds carry double spaces, newlines and
    non-breaking spaces routinely.
    """
    from scanner.llm.grounding import occurs_verbatim

    class Item:
        title = "Will GLP-1s hurt food stocks?  Assessing the changing consumer."
        summary = ""

    assert occurs_verbatim(
        "Will GLP-1s hurt food stocks? Assessing the changing consumer.", Item())
    assert occurs_verbatim("Will GLP-1s\nhurt food stocks?", Item())
    # and it must still refuse an invention
    assert not occurs_verbatim("Will GLP-1s help food stocks?", Item())
