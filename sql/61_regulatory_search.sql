-- Cortex Search service over the regulatory corpus.
--
-- THE ATTRIBUTE LIST IS THE DELIVERABLE. Cortex Search returns only the columns
-- declared here and generates no references of its own, so if a field is missing
-- from this list the copilot physically cannot cite it. Every attribute below is
-- consumed by app/grounding.py when it builds a Citation.

CREATE OR REPLACE CORTEX SEARCH SERVICE M360MART.SERVING.SVC_REGULATORY
  ON chunk_text
  ATTRIBUTES chunk_id, doc_key, citation_id, section_label, heading, page_no,
             themes, authority, source_url, effective_date, licence
  WAREHOUSE = COMPUTE_WH
  TARGET_LAG = '1 day'
  COMMENT = 'Retrieval over public-domain PA regulation (CFR, CMS, OIG, Texas HB 3459). Attributes carry the full citation so every answer is verifiable.'
AS
  SELECT
    chunk_text,
    chunk_id,
    doc_key,
    citation_id,
    section_label,
    heading,
    page_no,
    themes,
    authority,
    source_url,
    effective_date,
    licence
  FROM M360MART.SERVING.REGULATORY_CHUNKS;
