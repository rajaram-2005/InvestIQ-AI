// Disposable in-memory test DB only; do not use to store real or personal data.
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite-pgvector';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
const port = Number(process.env.INVESTIQ_TEST_PORT || 15432);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid test port');
const db = await PGlite.create({ extensions: { vector } });
const server = new PGLiteSocketServer({ db, port, host: '127.0.0.1' });
await server.start();
console.log(`Disposable PGlite database listening on 127.0.0.1:${port}`);
async function stop() { await server.stop(); await db.close(); process.exit(0); }
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
