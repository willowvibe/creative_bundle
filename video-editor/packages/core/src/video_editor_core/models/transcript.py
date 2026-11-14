"""Transcript models."""

from __future__ import annotations

from pydantic import BaseModel


class Word(BaseModel):
    word: str
    start: float
    end: float
    probability: float | None = None


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = []


class Transcript(BaseModel):
    source_audio: str
    language: str
    language_probability: float | None = None
    word_timestamps: bool = True
    segments: list[Segment] = []
    words: list[Word] = []
