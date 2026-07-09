from __future__ import annotations


def mock_text(text: str) -> str:
    result: list[str] = []
    upper = True
    for char in text:
        if char.isalpha():
            result.append(char.upper() if upper else char.lower())
            upper = not upper
        else:
            result.append(char)
    return "".join(result)


def reverse_text(text: str) -> str:
    return text[::-1]
