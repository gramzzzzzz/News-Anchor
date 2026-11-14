-- A story cut by the story-length limit while its reports kept coming has its continuation(s) in a chain: chain_id is the first story of the chain.
-- Lists show a chain as one row (the same news twice otherwise).
ALTER TABLE stories ADD COLUMN IF NOT EXISTS chain_id bigint;
CREATE INDEX IF NOT EXISTS stories_chain ON stories (chain_id) WHERE chain_id IS NOT NULL;
