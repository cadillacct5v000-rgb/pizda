import {configuration} from './config.mjs';
import {createApp} from './app.mjs';

const config = configuration(), runtime = createApp(config, {requestShutdown: () => {void close();}});
const server = runtime.app.listen(config.port, config.host, () => process.stdout.write(`Video Flow: http://${config.host}:${config.port}\n`));
let closing = false;
async function close() { if (closing) return; closing = true; server.close(); await runtime.close(); server.closeAllConnections(); }
process.on('SIGINT', close); process.on('SIGTERM', close);
server.on('error', async (error) => { process.stderr.write(`Backend failed: ${error.code}\n`); await runtime.close(); process.exitCode = 1; });
