"""MGCV26 reactive setup observer.

A small service that watches Micro Gold (MGCV6) on Tradovate, auto-detects
4H AS/AR zones, 1H S/R levels, and 4H/1H trend lines, then reacts to completed
5-minute bars that break/retest/bounce those lines. Qualifying setups are graded
A-/A/A+ and pushed to Telegram. Reactive, never predictive. Max 2 alerts/day.
"""
