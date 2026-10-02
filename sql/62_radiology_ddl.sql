-- M360MART radiology reference library objects
-- Deployed by dataPrepRadiology/load_radiology.py
--
-- These are PUBLIC TEACHING IMAGES for orientation only. They are never a member's
-- own studies and must not be used to support a determination. Every row carries the
-- licence and the attribution string required to display it.

CREATE SCHEMA IF NOT EXISTS M360MART.SERVING;

-- The "folder" for reference images. DIRECTORY must be enabled so the app can mint a
-- presigned URL per image; without it GET_PRESIGNED_URL cannot resolve the file.
CREATE STAGE IF NOT EXISTS M360MART.SERVING.STG_RADIOLOGY
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Public-domain and CC BY radiology teaching images for analyst orientation. Share-alike licences are deliberately excluded.';

CREATE OR REPLACE TABLE M360MART.SERVING.RADIOLOGY_REFERENCE (
    image_id          VARCHAR(40)   NOT NULL COMMENT 'Deterministic id derived from the image sha256',
    file_name         VARCHAR(300)  NOT NULL COMMENT 'File name on STG_RADIOLOGY',
    body_region       VARCHAR(30)   NOT NULL COMMENT 'LUMBAR_SPINE | KNEE | CHEST | BRAIN',
    modality          VARCHAR(10)   NOT NULL COMMENT 'MRI | CT | XRAY',
    finding_class     VARCHAR(10)   NOT NULL COMMENT 'NORMAL baseline, or ABNORMAL comparison',
    title             VARCHAR(300)  NOT NULL COMMENT 'Source title as published',
    teaching_caption  VARCHAR(1000) NOT NULL COMMENT 'Orientation-only description. Templated, never model-generated, so it cannot drift into interpretation.',
    source_page_url   VARCHAR(600)  NOT NULL COMMENT 'Wikimedia Commons description page',
    source_file_url   VARCHAR(600)  NOT NULL COMMENT 'Original file URL',
    licence           VARCHAR(100)  NOT NULL COMMENT 'CC0, Public domain or CC BY only. Enforced by config.app_config.licence_allowed().',
    attribution       VARCHAR(300)  NOT NULL COMMENT 'Attribution string that MUST be rendered beside the image',
    width             NUMBER(8,0)   NOT NULL COMMENT 'Pixel width after downscaling',
    height            NUMBER(8,0)   NOT NULL COMMENT 'Pixel height after downscaling',
    bytes             NUMBER(12,0)  NOT NULL COMMENT 'Stored size',
    sha256            VARCHAR(64)   NOT NULL COMMENT 'SHA-256 of the stored JPEG',
    ingested_at_utc   TIMESTAMP_NTZ NOT NULL COMMENT 'When the row was produced locally',
    CONSTRAINT pk_radiology_reference PRIMARY KEY (image_id)
)
COMMENT = 'Radiology teaching references, paired NORMAL vs ABNORMAL per body region, for analyst orientation only. Not diagnostic material and not member imaging.';
