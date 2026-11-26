-- Group F1-F4 normalization arrays and use repository-relative gallery paths.
-- Back up the database before running.
-- Existing masks, raw features, index snapshots, history and Z-score values are preserved.

BEGIN;
SELECT pg_advisory_xact_lock(hashtext('cbir_publish_index'));
LOCK TABLE feature_normalization, normalized_features, flower_images, retrieval_history IN ACCESS EXCLUSIVE MODE;

DO $migration$
DECLARE
    starts INTEGER[] := ARRAY[1,79,97,125];
    ends INTEGER[] := ARRAY[78,96,124,163];
    sizes INTEGER[] := ARRAY[78,18,28,39];
    group_number INTEGER;
    suffix TEXT;
    column_name_group TEXT;
    old_column TEXT;
    constraint_name_group TEXT;
    has_legacy BOOLEAN;
BEGIN
    FOR group_number IN 1..4 LOOP
        FOREACH suffix IN ARRAY ARRAY['means','stds'] LOOP
            column_name_group := format('f%s_%s', group_number, suffix);
            old_column := 'feature_' || suffix;
            EXECUTE format('ALTER TABLE feature_normalization ADD COLUMN IF NOT EXISTS %I DOUBLE PRECISION[]', column_name_group);
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name='feature_normalization'
                  AND column_name=old_column
            ) INTO has_legacy;
            IF has_legacy THEN
                EXECUTE format('UPDATE feature_normalization SET %I=%I[%s:%s]',
                    column_name_group,old_column,starts[group_number],ends[group_number]);
            END IF;
            EXECUTE format('ALTER TABLE feature_normalization ALTER COLUMN %I SET NOT NULL',column_name_group);
            constraint_name_group := column_name_group || '_length_check';
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid='feature_normalization'::regclass AND conname=constraint_name_group
            ) THEN
                EXECUTE format('ALTER TABLE feature_normalization ADD CONSTRAINT %I CHECK (cardinality(%I)=%s)',
                    constraint_name_group,column_name_group,sizes[group_number]);
            END IF;
        END LOOP;

        column_name_group := format('f%s_vector',group_number);
        EXECUTE format('ALTER TABLE normalized_features ADD COLUMN IF NOT EXISTS %I DOUBLE PRECISION[]',column_name_group);
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='normalized_features'
              AND column_name='normalized_vector'
        ) INTO has_legacy;
        IF has_legacy THEN
            EXECUTE format('UPDATE normalized_features SET %I=normalized_vector[%s:%s]',
                column_name_group,starts[group_number],ends[group_number]);
        END IF;
        EXECUTE format('ALTER TABLE normalized_features ALTER COLUMN %I SET NOT NULL',column_name_group);
        constraint_name_group := column_name_group || '_length_check';
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid='normalized_features'::regclass AND conname=constraint_name_group
        ) THEN
            EXECUTE format('ALTER TABLE normalized_features ADD CONSTRAINT %I CHECK (cardinality(%I)=%s)',
                constraint_name_group,column_name_group,sizes[group_number]);
        END IF;
    END LOOP;
END
$migration$;

ALTER TABLE feature_normalization
    DROP COLUMN IF EXISTS feature_means,
    DROP COLUMN IF EXISTS feature_stds;
ALTER TABLE normalized_features DROP COLUMN IF EXISTS normalized_vector;
ALTER TABLE flower_images DROP COLUMN IF EXISTS image_data;
ALTER TABLE retrieval_history DROP COLUMN IF EXISTS query_image_data;
DROP TABLE IF EXISTS flower_masks;

UPDATE flower_images SET image_path = 'dataset/images/' || filename;
COMMIT;
