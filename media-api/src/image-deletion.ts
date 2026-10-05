import { unlink } from "node:fs/promises";
import path from "node:path";
import { Pool } from "pg";

// Record cleanup in the same transaction as metadata deletion. Disk errors are
// retried after restart instead of leaving an untracked file behind.
export async function initImageDeletion(pool: Pool) {
  await pool.query(`CREATE TABLE IF NOT EXISTS media_file_deletions (
    storage_key TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )`);
}

export async function cleanupDeletedImages(pool: Pool, mediaDir: string) {
  const pending = await pool.query(`SELECT storage_key FROM media_file_deletions ORDER BY created_at LIMIT 100`);
  for (const row of pending.rows as { storage_key: string }[]) {
    try {
      const key = row.storage_key;
      if (!key || key !== path.basename(key) || key === "." || key === "..") throw new Error("Invalid image storage key");
      try { await unlink(path.join(mediaDir, key)); }
      catch (error) { if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error; }
      await pool.query(`DELETE FROM media_file_deletions WHERE storage_key=$1`, [key]);
    } catch (error) {
      console.error("Image file cleanup will be retried:", row.storage_key, error);
    }
  }
}

export async function deleteMediaImage(pool: Pool, mediaDir: string, id: string) {
  const client = await pool.connect();
  let deleted = false;
  try {
    await client.query("BEGIN");
    const result = await client.query(`DELETE FROM media_images WHERE id=$1 RETURNING storage_key`, [id]);
    const key: string | undefined = result.rows[0]?.storage_key;
    if (key) {
      if (key !== path.basename(key) || key === "." || key === "..") throw new Error("Invalid image storage key");
      await client.query(`INSERT INTO media_file_deletions(storage_key) VALUES($1) ON CONFLICT DO NOTHING`, [key]);
      deleted = true;
    }
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
  try { await cleanupDeletedImages(pool, mediaDir); }
  catch (error) { console.error("Image cleanup queued for retry:", error); }
  return deleted;
}
