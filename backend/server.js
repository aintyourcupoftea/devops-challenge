const express = require("express");
const morgan = require("morgan");
const { Pool } = require("pg");

const PORT = process.env.PORT || 3000;
const APP_VERSION = process.env.APP_VERSION || "dev";

const pool = new Pool({
  host: process.env.PGHOST,
  port: Number(process.env.PGPORT || 5432),
  user: process.env.PGUSER,
  password: process.env.PGPASSWORD,
  database: process.env.PGDATABASE,
  connectionTimeoutMillis: 2000,
});

const app = express();
app.use(express.json());
app.use(morgan("combined"));

let dbReady = false;

async function initDb(retries = 10, delayMs = 3000) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      await pool.query(`
        CREATE TABLE IF NOT EXISTS items (
          id SERIAL PRIMARY KEY,
          name TEXT NOT NULL,
          created_at TIMESTAMPTZ DEFAULT now()
        );
      `);
      dbReady = true;
      console.log("DB schema ready");
      return;
    } catch (err) {
      console.error(`DB init attempt ${attempt}/${retries} failed: ${err.message}`);
      if (attempt === retries) throw err;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
}

// Liveness: is the node process itself alive and able to serve requests?
// Deliberately does NOT touch the database - a slow/down DB should not
// convince Kubernetes to kill and restart a perfectly healthy process.
app.get("/healthz/live", (req, res) => {
  res.status(200).json({ status: "alive", version: APP_VERSION });
});

// Readiness: can this pod actually serve traffic right now?
// Touches the database on every call - if the DB is unreachable we want
// Kubernetes to pull this pod out of the Service endpoints immediately.
app.get("/healthz/ready", async (req, res) => {
  try {
    await pool.query("SELECT 1");
    res.status(200).json({ status: "ready", db: "connected" });
  } catch (err) {
    res.status(503).json({ status: "not-ready", db: "unreachable", error: err.message });
  }
});

app.get("/", (req, res) => {
  res.json({ service: "devops-challenge-backend", version: APP_VERSION, dbReady });
});

app.get("/items", async (req, res) => {
  try {
    const result = await pool.query("SELECT id, name, created_at FROM items ORDER BY id DESC LIMIT 50");
    res.json(result.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/items", async (req, res) => {
  const { name } = req.body || {};
  if (!name) return res.status(400).json({ error: "name is required" });
  try {
    const result = await pool.query(
      "INSERT INTO items (name) VALUES ($1) RETURNING id, name, created_at",
      [name]
    );
    res.status(201).json(result.rows[0]);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`Server listening on port ${PORT}, version ${APP_VERSION}`);
  initDb().catch((err) => {
    console.error("Failed to initialize DB schema:", err.message);
  });
});
