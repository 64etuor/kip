BEGIN;

ALTER TABLE source.objects
    ADD COLUMN data_classification text NOT NULL DEFAULT 'restricted';
ALTER TABLE content.units
    ADD COLUMN data_classification text NOT NULL DEFAULT 'restricted';

ALTER TABLE source.objects
    ADD CONSTRAINT source_objects_data_classification_check
    CHECK (
        data_classification IN (
            'public', 'internal', 'confidential', 'restricted', 'personal'
        )
    );
ALTER TABLE content.units
    ADD CONSTRAINT content_units_data_classification_check
    CHECK (
        data_classification IN (
            'public', 'internal', 'confidential', 'restricted', 'personal'
        )
    );

CREATE INDEX source_objects_classification_idx
    ON source.objects (workspace_id, data_classification);
CREATE INDEX content_units_classification_idx
    ON content.units (workspace_id, data_classification, id);

COMMIT;
