import { existsSync, readFileSync } from 'node:fs'
import { copyFile, rm } from 'node:fs/promises'
import path from 'node:path'

type Command = string[]

const root = path.resolve(import.meta.dir, '..')
const server = path.join(root, 'server')
const web = path.join(root, 'web')
const venv = path.join(server, 'venv')
const venvPython = path.join(
  venv,
  process.platform === 'win32' ? 'Scripts' : 'bin',
  process.platform === 'win32' ? 'python.exe' : 'python',
)
const compileTargets = ["server.py","agent.py","llm.py"]
const requiredEnv = ["AGORA_APP_ID","AGORA_APP_CERTIFICATE","CUSTOM_LLM_URL"]
const publicUrlKey = "CUSTOM_LLM_URL"
const requirements = ["requirements.txt","requirements-dev.txt"]
const setupMessage = "\nSetup complete! Next steps:\n   1. agora project env write server/.env.local   (or fill it in manually)\n   2. ngrok http 8000                            (expose the backend; Agora cloud calls /audio)\n   3. Add CUSTOM_LLM_URL=<tunnel-url>/audio/chat/completions to server/.env.local\n   4. bun run dev\n\n"

async function run(command: Command, cwd = root, env = process.env) {
  const child = Bun.spawn(command, {
    cwd,
    env,
    stdin: 'inherit',
    stdout: 'inherit',
    stderr: 'inherit',
  })
  const exitCode = await child.exited
  if (exitCode !== 0) throw new Error(`${command[0]} exited with code ${exitCode}`)
}

function pythonVersion(command: Command) {
  try {
    const result = Bun.spawnSync({ cmd: [...command, '--version'], stdout: 'pipe', stderr: 'pipe' })
    if (result.exitCode !== 0) return null
    const output = `${result.stdout.toString()} ${result.stderr.toString()}`
    const match = output.match(/Python\s+(\d+)\.(\d+)(?:\.(\d+))?/)
    if (!match) return null
    return {
      display: match[0],
      supported: Number(match[1]) > 3 || (Number(match[1]) === 3 && Number(match[2]) >= 10),
    }
  } catch {
    return null
  }
}

function commandSucceeds(command: Command) {
  try {
    return Bun.spawnSync({ cmd: command, stdout: 'ignore', stderr: 'ignore' }).exitCode === 0
  } catch {
    return false
  }
}

function findPython(): Command {
  const candidates: Command[] = process.env.PYTHON ? [[process.env.PYTHON]] : []
  if (process.platform === 'win32') {
    candidates.push(
      ['python'],
      ['py', '-3.13'],
      ['py', '-3.12'],
      ['py', '-3.11'],
      ['py', '-3.10'],
      ['py', '-3'],
    )
  } else {
    candidates.push(['python3'], ['python'])
  }

  const command = candidates.find((candidate) => pythonVersion(candidate)?.supported)
  if (!command) throw new Error('Python 3.10+ was not found. Set PYTHON to its executable path.')
  return command
}

function requireVenv() {
  if (!existsSync(venvPython)) throw new Error('Missing server virtualenv. Run bun run setup:server first.')
  const version = pythonVersion([venvPython])
  if (!version?.supported) throw new Error('The server virtualenv must use Python 3.10+.')
  return version.display
}

async function setupEnv() {
  const target = path.join(server, '.env.local')
  if (existsSync(target)) {
    console.log('Environment file already exists: server/.env.local')
    return
  }
  await copyFile(path.join(server, '.env.example'), target)
  console.log('Created server/.env.local from server/.env.example')
}

async function setupDependencies() {
  if (existsSync(path.join(root, 'node_modules'))) {
    console.log('Workspace dependencies are already installed')
    return
  }
  await run([process.execPath, 'install'])
}

async function setupServer(recreate = false, quiet = false, upgradePip = true, includeDev = true) {
  const hasVenv = existsSync(venv)
  const validPython = existsSync(venvPython) && pythonVersion([venvPython])?.supported
  const validPip = validPython && commandSucceeds([venvPython, '-m', 'pip', '--version'])
  if (recreate || (hasVenv && !validPip)) {
    await rm(venv, { recursive: true, force: true })
  }
  if (!existsSync(venvPython)) {
    const python = findPython()
    console.log(`Creating server virtualenv with ${pythonVersion(python)?.display}`)
    await run([...python, '-m', 'venv', venv])
  }

  requireVenv()
  if (upgradePip) await run([venvPython, '-m', 'pip', 'install', '--upgrade', 'pip'])
  const install = [venvPython, '-m', 'pip', 'install']
  if (quiet) install.push('-q')
  for (const requirement of includeDev ? requirements : requirements.slice(0, 1)) {
    install.push('-r', path.join(server, requirement))
  }
  await run(install, root, {
    ...process.env,
    PIP_INDEX_URL: process.env.PIP_INDEX_URL ?? 'https://pypi.org/simple',
  })
}

async function runBackend(withSetup: boolean) {
  if (withSetup) await setupServer(false, true, false, false)
  else requireVenv()
  await run([venvPython, 'src/server.py'], server)
}

function doctor(local = false) {
  console.log(`- Bun ${Bun.version}`)
  if (!existsSync(path.join(root, 'node_modules'))) throw new Error('Run bun install first.')
  console.log('- workspace dependencies installed')
  if (!local) return

  console.log(`- ${requireVenv()} server virtualenv available`)
  const envPath = path.join(server, '.env.local')
  if (!existsSync(envPath)) throw new Error('Missing server/.env.local')
  const file = readFileSync(envPath, 'utf8')
  for (const key of requiredEnv) {
    const value = file.match(new RegExp(`^${key}=(.+)$`, 'm'))?.[1]
    if (!value?.trim()) throw new Error(`${key} is missing in server/.env.local`)
    console.log(`- ${key} configured`)
  }
  if (publicUrlKey) {
    const value = file.match(new RegExp(`^${publicUrlKey}=(.+)$`, 'mi'))?.[1]
    if (value && /(localhost|127[.]0[.]0[.]1)/i.test(value)) {
      console.warn(`- WARNING: ${publicUrlKey} points at localhost; use a public tunnel URL.`)
    }
  }
}

async function verifyBackend() {
  requireVenv()
  await run([venvPython, '-m', 'py_compile', ...compileTargets], path.join(server, 'src'))
}

async function verifyBackendPytest() {
  requireVenv()
  await run([venvPython, '-m', 'pytest', 'tests', '-q'], server)
}

async function clean() {
  const targets = [
    venv,
    path.join(server, '__pycache__'),
    path.join(server, 'src', '__pycache__'),
    path.join(server, 'tests', '__pycache__'),
    path.join(root, 'node_modules'),
    path.join(web, 'node_modules'),
    path.join(web, '.next'),
    path.join(web, 'dist'),
  ]
  await Promise.all(targets.map((target) => rm(target, { recursive: true, force: true })))
}

const command = process.argv[2]
try {
  switch (command) {
    case 'setup-env': await setupEnv(); break
    case 'setup-deps': await setupDependencies(); break
    case 'setup-server': await setupServer(true); break
    case 'setup-done': console.log(setupMessage); break
    case 'dev-backend': await runBackend(true); break
    case 'backend': await runBackend(false); break
    case 'doctor': doctor(); break
    case 'doctor-local': doctor(true); break
    case 'verify-backend': await verifyBackend(); break
    case 'verify-backend-pytest': await verifyBackendPytest(); break
    case 'clean': await clean(); break
    default: throw new Error(`Unknown task: ${command ?? '(missing)'}`)
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  process.exit(1)
}
