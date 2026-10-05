import { randomUUID, timingSafeEqual } from "node:crypto";
import { mkdir, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import express, { Request, Response } from "express";
import multer from "multer";
import sharp from "sharp";
import { ApolloServer } from "@apollo/server";
import { expressMiddleware } from "@as-integrations/express4";
import { Pool } from "pg";

const PORT = Number(process.env.PORT || 4000);
const MEDIA_DIR = path.resolve(process.env.MEDIA_DIR || "/media");
const MAX_UPLOAD_MB = Math.max(1, Number(process.env.MAX_UPLOAD_MB || 20));
const MEDIA_PROXY_SECRET = process.env.MEDIA_PROXY_SECRET || "";
const pool = new Pool({
  host: process.env.PGHOST || "media-db",
  port: Number(process.env.PGPORT || 5432),
  database: process.env.PGDATABASE || "image_library",
  user: process.env.PGUSER || "image_library",
  password: process.env.PGPASSWORD,
  max: 10,
});

const typeDefs = `#graphql
  type Collection {
    id: ID!
    name: String!
    description: String!
    parentId: ID
    createdAt: String!
    children: [Collection!]!
    images: [MediaImage!]!
  }

  type MediaImage {
    id: ID!
    filename: String!
    title: String!
    description: String!
    mimeType: String!
    size: Int!
    width: Int
    height: Int
    createdAt: String!
    url: String!
    position: Int
  }

  type Query {
    collections(parentId: ID): [Collection!]!
    collection(id: ID!): Collection
    images(collectionId: ID!): [MediaImage!]!
  }

  type Mutation {
    createCollection(name: String!, description: String, parentId: ID): Collection!
    updateCollection(id: ID!, name: String!, description: String): Collection!
    deleteCollection(id: ID!): Boolean!
    addImageToCollection(collectionId: ID!, imageId: ID!): Boolean!
    updateImage(id: ID!, title: String!, description: String): MediaImage!
    deleteImage(id: ID!): Boolean!
    removeImageFromCollection(collectionId: ID!, imageId: ID!): Boolean!
    moveImage(collectionId: ID!, imageId: ID!, position: Int!): Boolean!
  }
`;

const collectionFields = `id, name, description, parent_id AS "parentId", created_at AS "createdAt"`;
const imageFields = `id, filename, title, description, mime_type AS "mimeType", size, width, height, created_at AS "createdAt"`;

async function initDatabase() {
  await mkdir(MEDIA_DIR, { recursive: true });
  await pool.query(`
    CREATE TABLE IF NOT EXISTS collections (
      id UUID PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      parent_id UUID REFERENCES collections(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS collections_parent_idx ON collections(parent_id);
    CREATE TABLE IF NOT EXISTS media_images (
      id UUID PRIMARY KEY,
      storage_key TEXT NOT NULL UNIQUE,
      filename TEXT NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      mime_type TEXT NOT NULL,
      size BIGINT NOT NULL,
      width INTEGER,
      height INTEGER,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS collection_images (
      collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
      image_id UUID NOT NULL REFERENCES media_images(id) ON DELETE CASCADE,
      position INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (collection_id, image_id)
    );
    CREATE INDEX IF NOT EXISTS collection_images_order_idx ON collection_images(collection_id, position);
  `);
}

async function imageById(id: string) {
  const result = await pool.query(`SELECT ${imageFields}, storage_key AS "storageKey" FROM media_images WHERE id=$1`, [id]);
  return result.rows[0] || null;
}

const resolvers = {
  Query: {
    collections: async (_: unknown, args: { parentId?: string | null }) => {
      const result = args.parentId
        ? await pool.query(`SELECT ${collectionFields} FROM collections WHERE parent_id=$1 ORDER BY created_at, name`, [args.parentId])
        : await pool.query(`SELECT ${collectionFields} FROM collections WHERE parent_id IS NULL ORDER BY created_at, name`);
      return result.rows;
    },
    collection: async (_: unknown, args: { id: string }) => {
      const result = await pool.query(`SELECT ${collectionFields} FROM collections WHERE id=$1`, [args.id]);
      return result.rows[0] || null;
    },
    images: async (_: unknown, args: { collectionId: string }) => {
      const result = await pool.query(
        `SELECT ${imageFields}, ci.position FROM collection_images ci JOIN media_images i ON i.id=ci.image_id WHERE ci.collection_id=$1 ORDER BY ci.position, i.created_at`,
        [args.collectionId],
      );
      return result.rows;
    },
  },
  Collection: {
    createdAt: (row: { createdAt: Date | string }) => new Date(row.createdAt).toISOString(),
    children: async (row: { id: string }) => {
      const result = await pool.query(`SELECT ${collectionFields} FROM collections WHERE parent_id=$1 ORDER BY created_at, name`, [row.id]);
      return result.rows;
    },
    images: async (row: { id: string }) => {
      const result = await pool.query(
        `SELECT ${imageFields}, ci.position FROM collection_images ci JOIN media_images i ON i.id=ci.image_id WHERE ci.collection_id=$1 ORDER BY ci.position, i.created_at`,
        [row.id],
      );
      return result.rows;
    },
  },
  MediaImage: {
    url: (row: { id: string }) => `/media/${row.id}`,
    size: (row: { size: string | number }) => Number(row.size),
    createdAt: (row: { createdAt: Date | string }) => new Date(row.createdAt).toISOString(),
  },
  Mutation: {
    createCollection: async (_: unknown, args: { name: string; description?: string | null; parentId?: string | null }) => {
      const result = await pool.query(
        `INSERT INTO collections(id,name,description,parent_id) VALUES($1,$2,$3,$4) RETURNING ${collectionFields}`,
        [randomUUID(), args.name.trim(), args.description?.trim() || "", args.parentId || null],
      );
      return result.rows[0];
    },
    updateCollection: async (_: unknown, args: { id: string; name: string; description?: string | null }) => {
      const result = await pool.query(
        `UPDATE collections SET name=$2, description=$3 WHERE id=$1 RETURNING ${collectionFields}`,
        [args.id, args.name.trim(), args.description?.trim() || ""],
      );
      if (!result.rowCount) throw new Error("Collection not found");
      return result.rows[0];
    },
    deleteCollection: async (_: unknown, args: { id: string }) => {
      const client = await pool.connect();
      let orphaned: string[] = [];
      try {
        await client.query("BEGIN");
        const check = await client.query(`SELECT id FROM collections WHERE id=$1`, [args.id]);
        if (!check.rowCount) { await client.query("ROLLBACK"); return false; }
        const affected = await client.query(`WITH RECURSIVE tree AS (SELECT id FROM collections WHERE id=$1 UNION ALL SELECT c.id FROM collections c JOIN tree t ON c.parent_id=t.id) SELECT DISTINCT ci.image_id FROM collection_images ci JOIN tree t ON t.id=ci.collection_id`, [args.id]);
        await client.query(`DELETE FROM collections WHERE id=$1`, [args.id]);
        const candidates = affected.rows.map((row: { image_id: string }) => row.image_id);
        if (candidates.length) {
          const deleted = await client.query(`DELETE FROM media_images i WHERE i.id=ANY($1::uuid[]) AND NOT EXISTS (SELECT 1 FROM collection_images ci WHERE ci.image_id=i.id) RETURNING storage_key`, [candidates]);
          orphaned = deleted.rows.map((row: { storage_key: string }) => row.storage_key);
        }
        await client.query("COMMIT");
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
      await Promise.all(orphaned.map((key) => unlink(path.join(MEDIA_DIR, key)).catch(() => undefined)));
      return true;
    },
    addImageToCollection: async (_: unknown, args: { collectionId: string; imageId: string }) => {
      const position = await pool.query(`SELECT COALESCE(MAX(position),-1)+1 AS position FROM collection_images WHERE collection_id=$1`, [args.collectionId]);
      const result = await pool.query(`INSERT INTO collection_images(collection_id,image_id,position) VALUES($1,$2,$3) ON CONFLICT DO NOTHING`, [args.collectionId, args.imageId, position.rows[0].position]);
      return (result.rowCount || 0) > 0;
    },
    updateImage: async (_: unknown, args: { id: string; title: string; description?: string | null }) => {
      const result = await pool.query(
        `UPDATE media_images SET title=$2, description=$3 WHERE id=$1 RETURNING ${imageFields}`,
        [args.id, args.title.trim(), args.description?.trim() || ""],
      );
      if (!result.rowCount) throw new Error("Image not found");
      return result.rows[0];
    },
    deleteImage: async (_: unknown, args: { id: string }) => {
      const row = await imageById(args.id);
      if (!row) return false;
      await pool.query(`DELETE FROM media_images WHERE id=$1`, [args.id]);
      await unlink(path.join(MEDIA_DIR, row.storageKey)).catch(() => undefined);
      return true;
    },
    removeImageFromCollection: async (_: unknown, args: { collectionId: string; imageId: string }) => {
      const client = await pool.connect();
      let storageKey: string | undefined;
      let removed = false;
      try {
        await client.query("BEGIN");
        const link = await client.query(`DELETE FROM collection_images WHERE collection_id=$1 AND image_id=$2`, [args.collectionId, args.imageId]);
        removed = (link.rowCount || 0) > 0;
        if (removed) {
          const orphan = await client.query(
            `DELETE FROM media_images i WHERE i.id=$1 AND NOT EXISTS (SELECT 1 FROM collection_images ci WHERE ci.image_id=i.id) RETURNING storage_key`,
            [args.imageId],
          );
          storageKey = orphan.rows[0]?.storage_key;
        }
        await client.query("COMMIT");
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
      if (storageKey) await unlink(path.join(MEDIA_DIR, storageKey)).catch(() => undefined);
      return removed;
    },
    moveImage: async (_: unknown, args: { collectionId: string; imageId: string; position: number }) => {
      const client = await pool.connect();
      try {
        await client.query("BEGIN");
        const rows = await client.query(`SELECT image_id FROM collection_images WHERE collection_id=$1 ORDER BY position`, [args.collectionId]);
        const ids = rows.rows.map((r: { image_id: string }) => r.image_id);
        const oldIndex = ids.indexOf(args.imageId);
        if (oldIndex < 0) throw new Error("Image is not in this collection");
        ids.splice(oldIndex, 1);
        ids.splice(Math.min(Math.max(args.position, 0), ids.length), 0, args.imageId);
        for (let i = 0; i < ids.length; i++) {
          await client.query(`UPDATE collection_images SET position=$3 WHERE collection_id=$1 AND image_id=$2`, [args.collectionId, ids[i], i]);
        }
        await client.query("COMMIT");
        return true;
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    },
  },
};

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: MAX_UPLOAD_MB * 1024 * 1024, files: 1 },
  fileFilter: (_req, file, callback) => {
    if (["image/png", "image/jpeg", "image/webp"].includes(file.mimetype)) callback(null, true);
    else callback(new Error("Only PNG, JPEG, and WebP images are accepted"));
  },
});

const app = express();
app.disable("x-powered-by");
app.use(["/graphql", "/upload", "/media"], (req: Request, res: Response, next) => {
  const supplied = Buffer.from(req.header("X-Media-Proxy-Secret") || "");
  const expected = Buffer.from(MEDIA_PROXY_SECRET);
  if (!expected.length || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return res.sendStatus(401);
  next();
});
app.get("/health", (_req: Request, res: Response) => res.json({ status: "ok" }));
app.get("/media/:id", async (req: Request, res: Response) => {
  if (!/^[0-9a-f-]{36}$/i.test(req.params.id)) return res.sendStatus(404);
  try {
    const row = await imageById(req.params.id);
    if (!row) return res.sendStatus(404);
    return res.sendFile(path.join(MEDIA_DIR, row.storageKey), { headers: { "Cache-Control": "private, max-age=3600" } });
  } catch {
    return res.sendStatus(500);
  }
});

app.post("/upload", upload.single("file"), async (req: Request, res: Response) => {
  const file = req.file;
  if (!file) return res.status(400).json({ error: "Choose an image to upload" });
  const collectionId = String(req.body.collectionId || "");
  if (!collectionId) return res.status(400).json({ error: "Choose a collection first" });
  let metadata: sharp.Metadata;
  try {
    metadata = await sharp(file.buffer, { limitInputPixels: 50_000_000 }).metadata();
  } catch {
    return res.status(415).json({ error: "The uploaded file is not a valid image" });
  }
  const format = metadata.format;
  const mimeType = format === "jpeg" ? "image/jpeg" : format === "png" ? "image/png" : format === "webp" ? "image/webp" : null;
  if (!mimeType) return res.status(415).json({ error: "Only PNG, JPEG, and WebP images are accepted" });
  const id = randomUUID();
  const storageKey = `${id}.${format === "jpeg" ? "jpg" : format}`;
  const title = String(req.body.title || path.parse(file.originalname).name).slice(0, 200);
  const description = String(req.body.description || "").slice(0, 4000);
  const destination = path.join(MEDIA_DIR, storageKey);
  try {
    await writeFileSafe(destination, file.buffer);
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query(
        `INSERT INTO media_images(id,storage_key,filename,title,description,mime_type,size,width,height) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
        [id, storageKey, path.basename(file.originalname).slice(0, 255), title, description, mimeType, file.size, metadata.width || null, metadata.height || null],
      );
      const positionResult = await client.query(`SELECT COALESCE(MAX(position),-1)+1 AS position FROM collection_images WHERE collection_id=$1`, [collectionId]);
      await client.query(`INSERT INTO collection_images(collection_id,image_id,position) VALUES($1,$2,$3)`, [collectionId, id, positionResult.rows[0].position]);
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      await unlink(destination).catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
    const result = await imageById(id);
    return res.status(201).json({ ...result, url: `/media/${id}` });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Upload failed";
    return res.status(400).json({ error: message.includes("violates foreign key") ? "Collection not found" : message });
  }
});

async function writeFileSafe(destination: string, data: Buffer) {
  await writeFile(destination, data, { flag: "wx" });
}

async function main() {
  if (MEDIA_PROXY_SECRET.length < 32) throw new Error("MEDIA_PROXY_SECRET must contain at least 32 characters");
  await initDatabase();
  const server = new ApolloServer({ typeDefs, resolvers });
  await server.start();
  app.use(express.json({ limit: "1mb" }));
  app.use("/graphql", expressMiddleware(server));
  app.listen(PORT, "0.0.0.0", () => console.log(`Media API listening on ${PORT}`));
}

main().catch((error) => {
  console.error("Media API failed to start", error);
  process.exit(1);
});
