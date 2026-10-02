"""Marks `app` as a package so `from app import ...` resolves consistently.

Needed in Streamlit in Snowflake, where the app runs from a stage directory rather
than the repository root and implicit namespace packages are less reliable.
"""
