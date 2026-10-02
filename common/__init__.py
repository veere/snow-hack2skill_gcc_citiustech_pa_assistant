"""
Shared library code for the Member360 PA datamart pipeline.

Layer-specific generator scripts live in dataPrepBronze/, dataPrepSilver/ and
dataPrepGold/. This package holds the infrastructure all three share, so there
is exactly one implementation of seeding, date anchoring, parquet conformance,
validation and Snowflake loading.

Modules
-------
determinism  seeded RNG derivation and stable row hashing
dates        the deterministic date-anchor shift
fetch        cached HTTP download with fallback URLs and checksums
frames       parquet read/write that conforms a DataFrame to its frozen spec
checks       the validation framework (PK/FK/enum/null/range/invariant)
"""
