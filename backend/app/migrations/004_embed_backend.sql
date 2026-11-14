-- Vectors from different methods (model, hashed) are not comparable; remember which made each one, so a change of method rebuilds gradually and nothing is compared across methods.
ALTER TABLE stories ADD COLUMN embed_backend text;
UPDATE stories SET embed_backend = 'hashed' WHERE embedding IS NOT NULL;
