use chrono::Utc;
use mistralrs::{EmbeddingModelBuilder, IsqType, Model as MistralModel};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Instant, UNIX_EPOCH};

const MAX_HASH_FILES: usize = 6000;
const MAX_STRUCTURE_FILES: usize = 360;
const MAX_SOURCE_SNIPPETS: usize = 14;
const MAX_SNIPPET_CHARS: usize = 5000;
const MAX_README_CHARS: usize = 12_000;
const MAX_TOP_LANGUAGES: usize = 10;
const MAX_TOP_KEYWORDS: usize = 48;
const MIN_TOP_KEYWORD_COUNT: usize = 1;
const MAX_SUMMARY_TAGS: usize = 16;
const MAX_SUMMARY_CONCEPTS: usize = 40;
const MAX_SUMMARY_LANGUAGES: usize = 16;
const MAX_MINED_MOTIFS: usize = 12;

const PROJECT_INDICATORS: &[&str] = &[
    "Cargo.toml",
    "package.json",
    "go.mod",
    "dune-project",
    "requirements.txt",
    "pyproject.toml",
    "flake.nix",
    "mix.exs",
    "Gemfile",
    "pom.xml",
    "README.md",
    "README",
];

const SOURCE_EXTENSIONS: &[&str] = &[
    ".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".swift", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".ml", ".mli", ".scala", ".clj", ".cljs", ".sh", ".nix", ".sol", ".zig",
];

const IGNORE_DIRS: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    ".jj",
    "node_modules",
    "target",
    "dist",
    "build",
    "_build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".turbo",
    ".cache",
    "vendor",
    "coverage",
    "tmp",
];

#[derive(Debug, Clone)]
struct LocalEmbeddingConfig {
    model_id: String,
    isq: Option<String>,
    force_cpu: bool,
    hf_cache_path: Option<PathBuf>,
}

#[derive(Debug, Clone)]
enum EmbeddingBackend {
    Http { url: String },
    Local(LocalEmbeddingConfig),
}

#[derive(Debug, Clone)]
struct EmbeddingConfig {
    backend: EmbeddingBackend,
    model: String,
    instruction: String,
    timeout_secs: u64,
    motif_neighbors: usize,
    motif_min_sim: f64,
}

#[derive(Debug, Clone)]
struct Config {
    targets: Vec<PathBuf>,
    shadow_root: PathBuf,
    gemini_bin: String,
    force: bool,
    limit: usize,
    workers: usize,
    dry_run: bool,
    verbose: bool,
    trace_dir: Option<PathBuf>,
    embeddings: Option<EmbeddingConfig>,
}

#[derive(Debug, Clone)]
struct Snippet {
    path: String,
    content: String,
}

#[derive(Debug, Clone)]
struct ProjectContext {
    file_count: usize,
    structure: Vec<String>,
    readme: String,
    snippets: Vec<Snippet>,
    top_languages: Vec<String>,
    top_keywords: Vec<String>,
}

#[derive(Debug)]
struct SummaryRecord {
    path: PathBuf,
    data: Map<String, Value>,
    embedding: Option<Vec<f64>>,
    changed: bool,
}

struct SummaryGeneration {
    summary: Value,
    source: &'static str,
    reason: Option<String>,
    prompt: String,
    raw_output: Option<String>,
}

#[derive(Debug, Clone)]
struct ProjectTaskResult {
    idx: usize,
    path: PathBuf,
    status: String,
    error: Option<String>,
}

enum GhMode {
    NotGh,
    GhContainer,
    GhProject,
}

fn language_by_extension(ext: &str) -> Option<&'static str> {
    match ext {
        ".rs" => Some("Rust"),
        ".py" => Some("Python"),
        ".ts" | ".tsx" => Some("TypeScript"),
        ".js" | ".jsx" => Some("JavaScript"),
        ".go" => Some("Go"),
        ".java" => Some("Java"),
        ".kt" => Some("Kotlin"),
        ".swift" => Some("Swift"),
        ".c" => Some("C"),
        ".cc" | ".cpp" | ".hpp" => Some("C++"),
        ".h" => Some("C/C++"),
        ".ml" | ".mli" => Some("OCaml"),
        ".scala" => Some("Scala"),
        ".clj" => Some("Clojure"),
        ".cljs" => Some("ClojureScript"),
        ".sh" => Some("Shell"),
        ".nix" => Some("Nix"),
        ".sol" => Some("Solidity"),
        ".zig" => Some("Zig"),
        _ => None,
    }
}

fn category_rules() -> Vec<(&'static str, Vec<&'static str>)> {
    vec![
        (
            "Zero-Knowledge Proofs",
            vec![
                "zk",
                "zero-knowledge",
                "proof",
                "snark",
                "stark",
                "mina",
                "cryptography",
                "signature",
                "kimchi",
                "o1js",
                "arkworks",
                "poly-commit",
            ],
        ),
        (
            "Distributed Systems",
            vec![
                "consensus",
                "bft",
                "p2p",
                "distributed",
                "raft",
                "paxos",
                "gossip",
                "iroh",
                "network",
                "protocol",
                "rpc",
                "capnproto",
            ],
        ),
        (
            "Formal Methods",
            vec![
                "coq",
                "fstar",
                "lean",
                "hol4",
                "formal",
                "verification",
                "proof",
                "cakeml",
                "iris",
                "logic",
                "smt",
                "formal-methods",
                "separation-logic",
                "itree",
                "interaction-tree",
            ],
        ),
        (
            "Capability-Based Security",
            vec![
                "ocap",
                "goblins",
                "syrup",
                "security",
                "permission",
                "capability",
                "sandbox",
                "ocapn",
                "object-capability",
            ],
        ),
        (
            "Systems/Runtimes",
            vec![
                "runtime",
                "kernel",
                "scheduler",
                "memory",
                "ffi",
                "concurrency",
                "thread",
                "v8",
                "jit",
                "syscall",
                "dhttp",
                "elide",
                "graal",
            ],
        ),
        (
            "Compilers/PL",
            vec![
                "compiler",
                "parser",
                "ast",
                "bytecode",
                "interpreter",
                "typechecker",
                "language",
                "codegen",
                "llvm",
                "cranelift",
                "semantics",
                "lambda",
                "sheaf",
                "ergolang",
                "egglog",
                "e-graph",
                "equality-saturation",
            ],
        ),
        (
            "Simulation/Games",
            vec![
                "engine",
                "game",
                "voxel",
                "physics",
                "simulation",
                "rendering",
                "graphics",
                "shader",
                "worldgen",
                "achron",
                "veloren",
            ],
        ),
        (
            "Hardware/ISA",
            vec![
                "risc",
                "isa",
                "cpu",
                "hdl",
                "verilog",
                "fpga",
                "instruction",
                "assembler",
                "binary",
                "absolute-ass",
            ],
        ),
        (
            "AI/Agents",
            vec![
                "model",
                "training",
                "inference",
                "dataset",
                "vector",
                "embedding",
                "llm",
                "agent",
                "mcp",
                "contextos",
            ],
        ),
        (
            "Data/Databases",
            vec![
                "database",
                "lsm",
                "storage",
                "index",
                "query",
                "sql",
                "key-value",
                "persistence",
                "fjall",
                "rocksdb",
            ],
        ),
        (
            "Developer Tooling",
            vec![
                "cli",
                "tooling",
                "lsp",
                "editor",
                "build",
                "automation",
                "script",
                "format",
                "lint",
                "smlfmt",
            ],
        ),
        (
            "Web/App",
            vec![
                "http", "web", "frontend", "react", "next", "api", "server", "client", "ui", "ux",
            ],
        ),
    ]
}

fn default_targets(home: &Path) -> Vec<PathBuf> {
    vec![
        home.join("dev"),
        home.join("shitheap"),
        home.join("hellas"),
        home.join("elide"),
        home.join("src"),
    ]
}

fn print_usage() {
    println!(
        "resummarize - recursive project summarization\n\
Usage:\n\
  resummarize [options]\n\
\nOptions:\n\
  --target <path>              Target directory to scan (repeatable)\n\
  --shadow-root <path>         Summary output root (default: ~/.summarization)\n\
  --gemini-bin <binary>        Gemini CLI binary (default: gemini)\n\
  --force                      Regenerate all summaries\n\
  --limit <n>                  Process only first N projects\n\
  --workers <n>                Number of parallel project workers (default: CPU count)\n\
  --dry-run                    Show planned updates without writing\n\
  --verbose                    Enable extra logging\n\
  --trace-dir <path>           Write detailed run traces to this directory\n\
  --embed-url <url>            HTTP embedding endpoint (/embed, /v1/embeddings, /api/embed)\n\
  --embed-local-model <id>     Local in-process embedding model id/path (mistral.rs)\n\
  --embed-local-isq <type>     Optional local ISQ quantization (Q4K, Q8_0, ...)\n\
  --embed-local-force-cpu      Force CPU for local embedding model\n\
  --embed-local-hf-cache <p>   Optional local HF cache path for local model\n\
  --embed-model <name>         Embedding model id (default: Qwen/Qwen3-Embedding-4B)\n\
  --embed-instruction <text>   Instruction prefix for embedding queries\n\
  --embed-timeout <seconds>    Embedding request timeout for curl (default: 120)\n\
  --motif-neighbors <n>        Nearest neighbors for motif mining (default: 6)\n\
  --motif-min-sim <float>      Minimum cosine similarity for motif transfer (default: 0.63)\n\
  -h, --help                   Show this help"
    );
}

fn parse_env_bool(name: &str) -> bool {
    env::var(name)
        .ok()
        .map(|v| {
            let lower = v.trim().to_ascii_lowercase();
            matches!(lower.as_str(), "1" | "true" | "yes" | "on")
        })
        .unwrap_or(false)
}

fn parse_args(home: &Path) -> Result<Config, String> {
    let mut targets_raw: Vec<PathBuf> = Vec::new();
    let mut shadow_root = home.join(".summarization");
    let mut gemini_bin = String::from("gemini");
    let mut force = false;
    let mut limit = 0usize;
    let mut workers = env::var("STARMAP_WORKERS")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or_else(|| {
            thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1)
        });
    let mut dry_run = false;
    let mut verbose = false;
    let mut trace_dir = env::var("STARMAP_TRACE_DIR")
        .ok()
        .filter(|x| !x.trim().is_empty())
        .map(|x| expand_tilde(&x, home));

    let mut embed_url = env::var("STARMAP_EMBED_URL")
        .ok()
        .filter(|x| !x.trim().is_empty());
    let mut embed_local_model = env::var("STARMAP_EMBED_LOCAL_MODEL")
        .ok()
        .filter(|x| !x.trim().is_empty());
    let mut embed_local_isq = env::var("STARMAP_EMBED_LOCAL_ISQ")
        .ok()
        .filter(|x| !x.trim().is_empty());
    let mut embed_local_force_cpu = parse_env_bool("STARMAP_EMBED_LOCAL_FORCE_CPU");
    let mut embed_local_hf_cache = env::var("STARMAP_EMBED_LOCAL_HF_CACHE")
        .ok()
        .filter(|x| !x.trim().is_empty())
        .map(|x| expand_tilde(&x, home));
    let mut embed_model = env::var("STARMAP_EMBED_MODEL")
        .ok()
        .filter(|x| !x.trim().is_empty())
        .unwrap_or_else(|| "Qwen/Qwen3-Embedding-4B".to_string());
    let mut embed_instruction = env::var("STARMAP_EMBED_INSTRUCTION")
        .ok()
        .filter(|x| !x.trim().is_empty())
        .unwrap_or_else(|| {
            "Represent this software project summary for clustering and motif mining".to_string()
        });
    let mut embed_timeout_secs: u64 = env::var("STARMAP_EMBED_TIMEOUT")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(120);
    let mut motif_neighbors: usize = env::var("STARMAP_MOTIF_NEIGHBORS")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(6);
    let mut motif_min_sim: f64 = env::var("STARMAP_MOTIF_MIN_SIM")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(0.63);

    let args: Vec<String> = env::args().collect();
    let mut idx = 1usize;
    while idx < args.len() {
        match args[idx].as_str() {
            "-h" | "--help" => {
                print_usage();
                std::process::exit(0);
            }
            "--target" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --target".into());
                }
                targets_raw.push(expand_tilde(&args[idx], home));
            }
            "--shadow-root" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --shadow-root".into());
                }
                shadow_root = expand_tilde(&args[idx], home);
            }
            "--gemini-bin" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --gemini-bin".into());
                }
                gemini_bin = args[idx].clone();
            }
            "--force" => force = true,
            "--limit" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --limit".into());
                }
                limit = args[idx]
                    .parse::<usize>()
                    .map_err(|_| "--limit must be an integer".to_string())?;
            }
            "--workers" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --workers".into());
                }
                workers = args[idx]
                    .parse::<usize>()
                    .map_err(|_| "--workers must be an integer".to_string())?;
            }
            "--dry-run" => dry_run = true,
            "--verbose" => verbose = true,
            "--trace-dir" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --trace-dir".into());
                }
                trace_dir = Some(expand_tilde(&args[idx], home));
            }
            "--embed-url" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-url".into());
                }
                embed_url = Some(args[idx].clone());
            }
            "--embed-local-model" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-local-model".into());
                }
                embed_local_model = Some(args[idx].clone());
            }
            "--embed-local-isq" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-local-isq".into());
                }
                embed_local_isq = Some(args[idx].clone());
            }
            "--embed-local-force-cpu" => {
                embed_local_force_cpu = true;
            }
            "--embed-local-hf-cache" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-local-hf-cache".into());
                }
                embed_local_hf_cache = Some(expand_tilde(&args[idx], home));
            }
            "--embed-model" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-model".into());
                }
                embed_model = args[idx].clone();
            }
            "--embed-instruction" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-instruction".into());
                }
                embed_instruction = args[idx].clone();
            }
            "--embed-timeout" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --embed-timeout".into());
                }
                embed_timeout_secs = args[idx]
                    .parse::<u64>()
                    .map_err(|_| "--embed-timeout must be an integer".to_string())?;
            }
            "--motif-neighbors" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --motif-neighbors".into());
                }
                motif_neighbors = args[idx]
                    .parse::<usize>()
                    .map_err(|_| "--motif-neighbors must be an integer".to_string())?;
            }
            "--motif-min-sim" => {
                idx += 1;
                if idx >= args.len() {
                    return Err("missing value for --motif-min-sim".into());
                }
                motif_min_sim = args[idx]
                    .parse::<f64>()
                    .map_err(|_| "--motif-min-sim must be a number".to_string())?;
            }
            other => return Err(format!("unknown argument: {other}")),
        }
        idx += 1;
    }

    let targets: Vec<PathBuf> = if targets_raw.is_empty() {
        default_targets(home)
            .into_iter()
            .filter(|p| p.exists() && p.is_dir())
            .collect()
    } else {
        targets_raw
            .into_iter()
            .filter(|p| p.exists() && p.is_dir())
            .collect()
    };

    let embeddings = if let Some(local_model) = embed_local_model {
        Some(EmbeddingConfig {
            backend: EmbeddingBackend::Local(LocalEmbeddingConfig {
                model_id: local_model.clone(),
                isq: embed_local_isq,
                force_cpu: embed_local_force_cpu,
                hf_cache_path: embed_local_hf_cache,
            }),
            model: local_model,
            instruction: embed_instruction,
            timeout_secs: embed_timeout_secs.max(5),
            motif_neighbors: motif_neighbors.max(1),
            motif_min_sim: motif_min_sim.clamp(0.0, 1.0),
        })
    } else {
        embed_url.map(|url| EmbeddingConfig {
            backend: EmbeddingBackend::Http { url },
            model: embed_model,
            instruction: embed_instruction,
            timeout_secs: embed_timeout_secs.max(5),
            motif_neighbors: motif_neighbors.max(1),
            motif_min_sim: motif_min_sim.clamp(0.0, 1.0),
        })
    };

    Ok(Config {
        targets,
        shadow_root,
        gemini_bin,
        force,
        limit,
        workers: workers.max(1),
        dry_run,
        verbose,
        trace_dir,
        embeddings,
    })
}

fn expand_tilde(input: &str, home: &Path) -> PathBuf {
    if let Some(rest) = input.strip_prefix("~/") {
        return home.join(rest);
    }
    if input == "~" {
        return home.to_path_buf();
    }
    PathBuf::from(input)
}

fn is_source_extension(ext: &str) -> bool {
    SOURCE_EXTENSIONS.contains(&ext)
}

fn is_ignored_dir(name: &str) -> bool {
    IGNORE_DIRS.contains(&name)
}

fn read_visible_entries(path: &Path) -> Option<(Vec<String>, Vec<(String, PathBuf)>)> {
    let mut files = Vec::new();
    let mut dirs = Vec::new();

    let entries = fs::read_dir(path).ok()?;
    for entry in entries.flatten() {
        let file_name = entry.file_name();
        let Some(name) = file_name.to_str() else {
            continue;
        };
        if name.starts_with('.') {
            continue;
        }
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if file_type.is_dir() {
            if is_ignored_dir(name) {
                continue;
            }
            dirs.push((name.to_string(), entry.path()));
        } else {
            files.push(name.to_string());
        }
    }

    files.sort();
    dirs.sort_by(|a, b| a.0.cmp(&b.0));
    Some((files, dirs))
}

fn looks_like_project(files: &[String], dir_names: &[String], path: &Path) -> bool {
    if PROJECT_INDICATORS
        .iter()
        .any(|indicator| files.iter().any(|f| f == indicator))
    {
        return true;
    }

    if path.join(".git").is_dir() {
        return true;
    }

    let source_count = files
        .iter()
        .filter(|f| {
            let ext = Path::new(f)
                .extension()
                .and_then(|x| x.to_str())
                .unwrap_or("");
            !ext.is_empty() && is_source_extension(&format!(".{ext}"))
        })
        .count();

    if source_count >= 4 {
        return true;
    }

    if dir_names
        .iter()
        .any(|d| ["src", "lib", "cmd", "app"].contains(&d.as_str()))
    {
        let mut score = source_count;
        if files.iter().any(|f| {
            f.ends_with(".toml")
                || f.ends_with(".yaml")
                || f.ends_with(".yml")
                || f.ends_with(".json")
        }) {
            score += 2;
        }
        return score >= 3;
    }

    false
}

fn gh_mode(path: &Path, gh_root: &Path, files: &[String], dirs: &[(String, PathBuf)]) -> GhMode {
    if path == gh_root {
        return GhMode::GhContainer;
    }

    let Ok(rel) = path.strip_prefix(gh_root) else {
        return GhMode::NotGh;
    };

    let depth = rel.components().count();
    if depth == 0 {
        return GhMode::GhContainer;
    }

    let has_git = path.join(".git").is_dir();
    if depth == 1 && files.is_empty() && !has_git && !dirs.is_empty() {
        return GhMode::GhContainer;
    }

    GhMode::GhProject
}

fn iter_project_roots(target: &Path, gh_root: &Path) -> Vec<PathBuf> {
    let mut projects = Vec::new();
    let mut stack = vec![target.to_path_buf()];

    while let Some(root) = stack.pop() {
        let Some((files, dirs)) = read_visible_entries(&root) else {
            continue;
        };

        match gh_mode(&root, gh_root, &files, &dirs) {
            GhMode::GhProject => {
                projects.push(root);
                continue;
            }
            GhMode::GhContainer => {}
            GhMode::NotGh => {
                let dir_names: Vec<String> = dirs.iter().map(|(n, _)| n.clone()).collect();
                if looks_like_project(&files, &dir_names, &root) {
                    projects.push(root);
                    continue;
                }
            }
        }

        for (_, child) in dirs.into_iter().rev() {
            stack.push(child);
        }
    }

    projects
}

fn rel_for_shadow(path: &Path, home: &Path) -> PathBuf {
    if let Ok(rel) = path.strip_prefix(home) {
        return rel.to_path_buf();
    }

    let mut rel = PathBuf::new();
    for component in path.components() {
        if let Component::Normal(seg) = component {
            rel.push(seg);
        }
    }
    if rel.as_os_str().is_empty() {
        if let Some(name) = path.file_name() {
            rel.push(name);
        } else {
            rel.push("project");
        }
    }
    rel
}

fn truncate_chars(input: &str, max_chars: usize) -> String {
    input.chars().take(max_chars).collect()
}

fn safe_read_text(path: &Path, max_chars: usize) -> String {
    let Ok(mut file) = File::open(path) else {
        return String::new();
    };

    let mut buf = Vec::new();
    if Read::by_ref(&mut file)
        .take((max_chars as u64) * 4 + 16)
        .read_to_end(&mut buf)
        .is_err()
    {
        return String::new();
    }

    truncate_chars(&String::from_utf8_lossy(&buf), max_chars)
}

fn walk_project_files(path: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut stack = vec![path.to_path_buf()];

    while let Some(root) = stack.pop() {
        let Some((file_names, dirs)) = read_visible_entries(&root) else {
            continue;
        };

        for file_name in file_names {
            files.push(root.join(file_name));
        }

        for (_, child) in dirs.into_iter().rev() {
            stack.push(child);
        }
    }

    files.sort();
    files
}

fn get_files_hash(path: &Path) -> String {
    let mut hasher = Sha256::new();
    let mut file_count = 0usize;

    for file in walk_project_files(path) {
        if file_count >= MAX_HASH_FILES {
            break;
        }
        file_count += 1;

        let rel = file
            .strip_prefix(path)
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|_| file.clone());

        if let Ok(meta) = file.metadata() {
            let modified = meta
                .modified()
                .ok()
                .and_then(|mtime| mtime.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_nanos())
                .unwrap_or(0);

            hasher.update(rel.to_string_lossy().as_bytes());
            hasher.update(modified.to_string().as_bytes());
            hasher.update(meta.len().to_string().as_bytes());
        }
    }

    hasher.update(file_count.to_string().as_bytes());
    format!("{:x}", hasher.finalize())
}

fn top_items(counter: &HashMap<String, usize>, max: usize, min_count: usize) -> Vec<String> {
    let mut items: Vec<(String, usize)> = counter
        .iter()
        .filter(|(_, count)| **count >= min_count)
        .map(|(k, v)| (k.clone(), *v))
        .collect();

    items.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    items.into_iter().take(max).map(|(k, _)| k).collect()
}

fn gather_project_context(path: &Path) -> ProjectContext {
    let mut structure: Vec<String> = Vec::new();
    let mut language_counter: HashMap<String, usize> = HashMap::new();
    let mut source_candidates: Vec<(u64, PathBuf)> = Vec::new();
    let mut keywords: HashMap<String, usize> = HashMap::new();

    let files = walk_project_files(path);
    let file_count = files.len();

    for file in &files {
        let rel = file
            .strip_prefix(path)
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|_| file.clone());

        if structure.len() < MAX_STRUCTURE_FILES {
            structure.push(rel.to_string_lossy().to_string());
        }

        if let Some(ext) = rel.extension().and_then(|e| e.to_str()) {
            let ext_dot = format!(".{}", ext.to_lowercase());
            if let Some(language) = language_by_extension(&ext_dot) {
                *language_counter.entry(language.to_string()).or_insert(0) += 1;
            }

            if is_source_extension(&ext_dot) {
                if let Ok(meta) = file.metadata() {
                    let size = meta.len();
                    if size > 0 && size <= 128_000 {
                        source_candidates.push((size, file.clone()));
                    }
                }
            }
        }

        if let Some(stem) = rel.file_stem().and_then(|s| s.to_str()) {
            for part in stem.replace('-', "_").split('_') {
                let lowered = part.to_lowercase();
                if lowered.len() >= 4 {
                    *keywords.entry(lowered).or_insert(0) += 1;
                }
            }
        }
    }

    let mut readme = String::new();
    for candidate in ["README.md", "README", "readme.md", "README.txt"] {
        let candidate_path = path.join(candidate);
        if candidate_path.exists() && candidate_path.is_file() {
            readme = safe_read_text(&candidate_path, MAX_README_CHARS);
            if !readme.trim().is_empty() {
                break;
            }
        }
    }

    let preferred_files = [
        "Cargo.toml",
        "package.json",
        "pyproject.toml",
        "go.mod",
        "dune-project",
        "flake.nix",
        "Makefile",
    ];

    let mut samples: Vec<PathBuf> = Vec::new();
    for pref in preferred_files {
        let candidate = path.join(pref);
        if candidate.exists() && candidate.is_file() {
            samples.push(candidate);
        }
    }

    source_candidates.sort_by(|a, b| b.0.cmp(&a.0));
    for (_, source) in source_candidates {
        if samples.len() >= MAX_SOURCE_SNIPPETS {
            break;
        }
        if !samples.iter().any(|x| x == &source) {
            samples.push(source);
        }
    }

    let mut snippets = Vec::new();
    for sample in samples.into_iter().take(MAX_SOURCE_SNIPPETS) {
        let rel = sample
            .strip_prefix(path)
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|_| sample.clone());
        let content = safe_read_text(&sample, MAX_SNIPPET_CHARS);
        if content.trim().is_empty() {
            continue;
        }
        snippets.push(Snippet {
            path: rel.to_string_lossy().to_string(),
            content,
        });
    }

    let top_languages = top_items(&language_counter, MAX_TOP_LANGUAGES, 1);
    let top_keywords = top_items(&keywords, MAX_TOP_KEYWORDS, MIN_TOP_KEYWORD_COUNT);

    ProjectContext {
        file_count,
        structure,
        readme,
        snippets,
        top_languages,
        top_keywords,
    }
}

fn infer_category(text: &str) -> String {
    let lower = text.to_lowercase();
    let mut best_category = "Exploratory";
    let mut best_score = 0usize;

    for (category, needles) in category_rules() {
        let score = needles
            .iter()
            .filter(|needle| lower.contains(**needle))
            .count();
        if score > best_score {
            best_score = score;
            best_category = category;
        }
    }

    best_category.to_string()
}

fn dedup_strings(values: Vec<String>, max: usize) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for value in values {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            continue;
        }
        let key = trimmed.to_lowercase();
        if seen.insert(key) {
            out.push(trimmed.to_string());
            if out.len() >= max {
                break;
            }
        }
    }
    out
}

fn fallback_summary(path: &Path, context: &ProjectContext) -> Value {
    let lang_text = if context.top_languages.is_empty() {
        "mixed tooling".to_string()
    } else {
        context
            .top_languages
            .iter()
            .take(3)
            .cloned()
            .collect::<Vec<_>>()
            .join(", ")
    };

    let keyword_text = if context.top_keywords.is_empty() {
        "repository structure and source files".to_string()
    } else {
        context
            .top_keywords
            .iter()
            .take(4)
            .cloned()
            .collect::<Vec<_>>()
            .join(", ")
    };

    let category = infer_category(&format!(
        "{} {} {}",
        path.file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("project"),
        context.top_keywords.join(" "),
        context.top_languages.join(" ")
    ));

    let description = format!(
        "{} appears to be a {} project using {}. The available files suggest focus areas around {}.",
        path.file_name().and_then(|s| s.to_str()).unwrap_or("This repository"),
        category.to_lowercase(),
        lang_text,
        keyword_text
    );

    let mut tags = context.top_languages.clone();
    tags.extend(context.top_keywords.clone());

    json!({
        "name": path.file_name().and_then(|s| s.to_str()).unwrap_or("Unnamed"),
        "description": description,
        "tags": dedup_strings(tags, MAX_SUMMARY_TAGS),
        "category": category,
        "concepts": dedup_strings(context.top_keywords.clone(), MAX_SUMMARY_CONCEPTS),
        "languages": dedup_strings(context.top_languages.clone(), MAX_SUMMARY_LANGUAGES),
        "maturity": "exploratory",
    })
}

fn build_prompt(path: &Path, context: &ProjectContext) -> String {
    let mut lines = Vec::new();
    lines.push(format!(
        "Project name: {}",
        path.file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("Unnamed")
    ));
    lines.push(format!("Project path: {}", path.display()));
    lines.push(format!("Approx file count: {}", context.file_count));
    lines.push(format!(
        "Top languages: {}",
        if context.top_languages.is_empty() {
            "unknown".to_string()
        } else {
            context.top_languages.join(", ")
        }
    ));
    lines.push(format!(
        "Top filename keywords: {}",
        if context.top_keywords.is_empty() {
            "none".to_string()
        } else {
            context.top_keywords.join(", ")
        }
    ));

    lines.push("\n=== File Structure (sample) ===".to_string());
    for item in &context.structure {
        lines.push(item.clone());
    }

    if !context.readme.trim().is_empty() {
        lines.push("\n=== README (truncated) ===".to_string());
        lines.push(context.readme.clone());
    }

    if !context.snippets.is_empty() {
        lines.push("\n=== Representative File Snippets ===".to_string());
        for snippet in &context.snippets {
            lines.push(format!("\n--- {} ---", snippet.path));
            lines.push(snippet.content.clone());
        }
    }

    let instructions = [
        "You are a repository summarization agent. Infer intent even if README is missing.",
        "Return ONLY valid JSON. Do not include markdown or prose outside JSON.",
        "Schema:",
        "{",
        "  \"name\": \"string\",",
        "  \"description\": \"one coherent paragraph, 2-4 sentences. Use technical, precise language suitable for a systems engineer.\",",
        "  \"tags\": [\"string\"],",
        "  \"category\": \"string. Derive the most specific and accurate technical domain from the evidence (e.g. 'Simplicial Homotopy Theory', 'Micro-architecture Emulation', 'CRDT Consensus'). Do NOT use generic labels like 'Math' or 'Software'.\",",
        "  \"concepts\": [\"string\"],",
        "  \"languages\": [\"string\"],",
        "  \"maturity\": \"one of: exploratory, prototype, active, stable, archival\"",
        "}",
        "You may define any category that fits best.",
        "Keep tags concrete and non-redundant (max 16). Keep concepts concrete and non-redundant (target 16-40 when evidence supports it).",
    ]
    .join("\n");

    format!("{}\n\nContext:\n{}", instructions, lines.join("\n"))
}

fn extract_json_object(text: &str) -> Option<String> {
    let start = text.find('{')?;
    let mut depth = 0i32;
    let mut in_string = false;
    let mut escape = false;

    for (idx, ch) in text.char_indices().skip(start) {
        if in_string {
            if escape {
                escape = false;
                continue;
            }
            if ch == '\\' {
                escape = true;
                continue;
            }
            if ch == '"' {
                in_string = false;
            }
            continue;
        }

        if ch == '"' {
            in_string = true;
            continue;
        }
        if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth -= 1;
            if depth == 0 {
                return Some(text[start..=idx].to_string());
            }
        }
    }

    None
}

fn value_string_list(value: Option<&Value>) -> Vec<String> {
    let Some(Value::Array(items)) = value else {
        return Vec::new();
    };
    items
        .iter()
        .filter_map(|item| item.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

fn sanitize_summary(path: &Path, raw: &Value, context: &ProjectContext) -> Value {
    let fallback = fallback_summary(path, context);

    let raw_obj = raw.as_object();
    let fallback_obj = fallback
        .as_object()
        .expect("fallback summary must be object");

    let name = raw_obj
        .and_then(|obj| obj.get("name"))
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            fallback_obj
                .get("name")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_else(|| {
            path.file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("Unnamed")
                .to_string()
        });

    let mut description = raw_obj
        .and_then(|obj| obj.get("description"))
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if description.split_whitespace().count() < 12 {
        description = fallback_obj
            .get("description")
            .and_then(|v| v.as_str())
            .unwrap_or("No description available")
            .to_string();
    }

    let mut tags = value_string_list(raw_obj.and_then(|obj| obj.get("tags")));
    let mut concepts = value_string_list(raw_obj.and_then(|obj| obj.get("concepts")));
    let mut languages = value_string_list(raw_obj.and_then(|obj| obj.get("languages")));

    if languages.is_empty() {
        languages = context.top_languages.clone();
    }
    if tags.is_empty() {
        tags = value_string_list(fallback_obj.get("tags"));
    }
    if concepts.is_empty() {
        concepts = value_string_list(fallback_obj.get("concepts"));
    }

    let mut category = raw_obj
        .and_then(|obj| obj.get("category"))
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_default();

    if category.is_empty() {
        category = infer_category(&format!(
            "{} {} {}",
            description,
            tags.join(" "),
            concepts.join(" ")
        ));
    }

    let maturity = raw_obj
        .and_then(|obj| obj.get("maturity"))
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "exploratory".to_string());

    json!({
        "name": name,
        "description": description,
        "tags": dedup_strings(tags, MAX_SUMMARY_TAGS),
        "category": category,
        "concepts": dedup_strings(concepts, MAX_SUMMARY_CONCEPTS),
        "languages": dedup_strings(languages, MAX_SUMMARY_LANGUAGES),
        "maturity": maturity,
        "evidence": {
            "file_count": context.file_count,
            "has_readme": !context.readme.trim().is_empty(),
            "sample_files": context
                .snippets
                .iter()
                .take(5)
                .map(|s| Value::String(s.path.clone()))
                .collect::<Vec<_>>(),
        }
    })
}

fn generate_summary(
    path: &Path,
    gemini_bin: &str,
    context: &ProjectContext,
    verbose: bool,
) -> SummaryGeneration {
    let prompt = build_prompt(path, context);
    let output = Command::new(gemini_bin)
        .current_dir(std::env::temp_dir())
        .arg("-p")
        .arg(&prompt)
        .arg("--output-format")
        .arg("text")
        .output();

    let Ok(result) = output else {
        if verbose {
            eprintln!("[warn] gemini binary not found or failed to execute: {gemini_bin}");
        }
        return SummaryGeneration {
            summary: fallback_summary(path, context),
            source: "fallback",
            reason: Some("gemini_exec_failed".to_string()),
            prompt,
            raw_output: None,
        };
    };

    if !result.status.success() {
        let stderr = String::from_utf8_lossy(&result.stderr).to_string();
        if verbose {
            eprintln!(
                "[warn] gemini failed for {}: {}",
                path.display(),
                truncate_chars(stderr.trim(), 400)
            );
        }
        return SummaryGeneration {
            summary: fallback_summary(path, context),
            source: "fallback",
            reason: Some(format!("gemini_nonzero_exit:{}", result.status)),
            prompt,
            raw_output: Some(stderr),
        };
    }

    let stdout = String::from_utf8_lossy(&result.stdout).to_string();
    let Some(json_blob) = extract_json_object(stdout.trim()) else {
        if verbose {
            eprintln!("[warn] no JSON detected for {}", path.display());
        }
        return SummaryGeneration {
            summary: fallback_summary(path, context),
            source: "fallback",
            reason: Some("gemini_no_json".to_string()),
            prompt,
            raw_output: Some(stdout),
        };
    };

    let Ok(parsed) = serde_json::from_str::<Value>(&json_blob) else {
        if verbose {
            eprintln!("[warn] invalid JSON for {}", path.display());
        }
        return SummaryGeneration {
            summary: fallback_summary(path, context),
            source: "fallback",
            reason: Some("gemini_invalid_json".to_string()),
            prompt,
            raw_output: Some(stdout),
        };
    };

    if !parsed.is_object() {
        return SummaryGeneration {
            summary: fallback_summary(path, context),
            source: "fallback",
            reason: Some("gemini_non_object_json".to_string()),
            prompt,
            raw_output: Some(stdout),
        };
    }

    SummaryGeneration {
        summary: sanitize_summary(path, &parsed, context),
        source: "gemini",
        reason: None,
        prompt,
        raw_output: Some(stdout),
    }
}

fn read_existing_hash(summary_file: &Path) -> Option<String> {
    let text = fs::read_to_string(summary_file).ok()?;
    let value: Value = serde_json::from_str(&text).ok()?;
    value
        .as_object()
        .and_then(|obj| obj.get("_hash"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

fn write_json(summary_file: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = summary_file.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("create dir failed: {err}"))?;
    }
    let mut file = File::create(summary_file).map_err(|err| format!("open failed: {err}"))?;
    let serialized =
        serde_json::to_string_pretty(value).map_err(|err| format!("json encode failed: {err}"))?;
    file.write_all(serialized.as_bytes())
        .map_err(|err| format!("write failed: {err}"))?;
    file.write_all(b"\n")
        .map_err(|err| format!("write newline failed: {err}"))?;
    Ok(())
}

fn context_to_trace_value(context: &ProjectContext) -> Value {
    json!({
        "file_count": context.file_count,
        "top_languages": context.top_languages,
        "top_keywords": context.top_keywords,
        "structure": context.structure,
        "readme": context.readme,
        "snippets": context
            .snippets
            .iter()
            .map(|snippet| {
                json!({
                    "path": snippet.path,
                    "content": snippet.content,
                })
            })
            .collect::<Vec<_>>(),
    })
}

fn write_project_trace(trace_root: Option<&Path>, idx: usize, trace: Value, verbose: bool) {
    let Some(root) = trace_root else {
        return;
    };
    let trace_path = root.join("projects").join(format!("{:06}.json", idx + 1));
    if let Err(err) = write_json(&trace_path, &trace) {
        if verbose {
            eprintln!(
                "[warn] failed writing project trace {}: {}",
                trace_path.display(),
                err
            );
        }
    }
}

fn process_project(
    path: &Path,
    idx: usize,
    home: &Path,
    cfg: &Config,
    trace_root: Option<&Path>,
) -> Result<String, String> {
    let started = Instant::now();
    let rel_path = rel_for_shadow(path, home);
    let summary_file = cfg.shadow_root.join(rel_path).join("summary.json");
    let path_string = path.to_string_lossy().to_string();
    let summary_file_string = summary_file.to_string_lossy().to_string();

    let project_hash = get_files_hash(path);
    let previous_hash = if !cfg.force {
        read_existing_hash(&summary_file)
    } else {
        None
    };

    if !cfg.force {
        if previous_hash.as_deref() == Some(project_hash.as_str()) {
            write_project_trace(
                trace_root,
                idx,
                json!({
                    "index": idx,
                    "path": path_string,
                    "summary_file": summary_file_string,
                    "status": "skip",
                    "reason": "hash_unchanged",
                    "project_hash": project_hash,
                    "previous_hash": previous_hash,
                    "elapsed_ms": started.elapsed().as_millis(),
                    "timestamp": Utc::now().to_rfc3339(),
                }),
                cfg.verbose,
            );
            return Ok("skip".to_string());
        }
    }

    if cfg.dry_run {
        write_project_trace(
            trace_root,
            idx,
            json!({
                "index": idx,
                "path": path_string,
                "summary_file": summary_file_string,
                "status": "would_update",
                "reason": "dry_run",
                "project_hash": project_hash,
                "previous_hash": previous_hash,
                "elapsed_ms": started.elapsed().as_millis(),
                "timestamp": Utc::now().to_rfc3339(),
            }),
            cfg.verbose,
        );
        return Ok("would_update".to_string());
    }

    let context = gather_project_context(path);
    let generation = generate_summary(path, &cfg.gemini_bin, &context, cfg.verbose);
    let mut summary = generation.summary;
    let summary_obj = summary
        .as_object_mut()
        .ok_or_else(|| "summary root is not object".to_string())?;

    summary_obj.insert("_hash".to_string(), Value::String(project_hash.clone()));
    summary_obj.insert("_path".to_string(), Value::String(path_string.clone()));
    summary_obj.insert(
        "_updated_at".to_string(),
        Value::String(Utc::now().to_rfc3339()),
    );

    write_json(&summary_file, &summary)?;
    write_project_trace(
        trace_root,
        idx,
        json!({
            "index": idx,
            "path": path_string,
            "summary_file": summary_file_string,
            "status": "updated",
            "project_hash": project_hash,
            "previous_hash": previous_hash,
            "generation_source": generation.source,
            "generation_reason": generation.reason,
            "prompt_hash": hash_text(&generation.prompt),
            "prompt": generation.prompt,
            "raw_output_hash": generation
                .raw_output
                .as_ref()
                .map(|value| hash_text(value)),
            "raw_output": generation.raw_output,
            "context": context_to_trace_value(&context),
            "summary": summary,
            "elapsed_ms": started.elapsed().as_millis(),
            "timestamp": Utc::now().to_rfc3339(),
        }),
        cfg.verbose,
    );
    Ok("updated".to_string())
}

fn process_projects(
    project_roots: &[PathBuf],
    home: &Path,
    cfg: &Config,
    trace_root: Option<&Path>,
) -> Vec<ProjectTaskResult> {
    if project_roots.is_empty() {
        return Vec::new();
    }

    let worker_count = cfg.workers.max(1).min(project_roots.len());
    if worker_count == 1 {
        let mut outcomes = Vec::with_capacity(project_roots.len());
        for (idx, project) in project_roots.iter().enumerate() {
            match process_project(project, idx, home, cfg, trace_root) {
                Ok(status) => outcomes.push(ProjectTaskResult {
                    idx,
                    path: project.clone(),
                    status,
                    error: None,
                }),
                Err(err) => outcomes.push(ProjectTaskResult {
                    idx,
                    path: project.clone(),
                    status: "error".to_string(),
                    error: Some(err),
                }),
            }
        }
        return outcomes;
    }

    let paths = Arc::new(project_roots.to_vec());
    let next_index = Arc::new(AtomicUsize::new(0));
    let outcomes = Arc::new(Mutex::new(Vec::<ProjectTaskResult>::new()));
    let mut handles = Vec::new();

    for _ in 0..worker_count {
        let paths = Arc::clone(&paths);
        let next_index = Arc::clone(&next_index);
        let outcomes = Arc::clone(&outcomes);
        let cfg_thread = cfg.clone();
        let home_thread = home.to_path_buf();
        let trace_root_thread = trace_root.map(|path| path.to_path_buf());

        handles.push(thread::spawn(move || loop {
            let idx = next_index.fetch_add(1, AtomicOrdering::Relaxed);
            if idx >= paths.len() {
                break;
            }

            let project = paths[idx].clone();
            let result = process_project(
                &project,
                idx,
                &home_thread,
                &cfg_thread,
                trace_root_thread.as_deref(),
            );

            let mut guard = match outcomes.lock() {
                Ok(guard) => guard,
                Err(poisoned) => poisoned.into_inner(),
            };
            match result {
                Ok(status) => guard.push(ProjectTaskResult {
                    idx,
                    path: project,
                    status,
                    error: None,
                }),
                Err(err) => guard.push(ProjectTaskResult {
                    idx,
                    path: project,
                    status: "error".to_string(),
                    error: Some(err),
                }),
            }
        }));
    }

    for handle in handles {
        if let Err(err) = handle.join() {
            let mut guard = match outcomes.lock() {
                Ok(guard) => guard,
                Err(poisoned) => poisoned.into_inner(),
            };
            guard.push(ProjectTaskResult {
                idx: usize::MAX,
                path: PathBuf::from("<worker>"),
                status: "error".to_string(),
                error: Some(format!("worker thread panicked: {err:?}")),
            });
        }
    }

    let mut out = match outcomes.lock() {
        Ok(guard) => guard.clone(),
        Err(poisoned) => poisoned.into_inner().clone(),
    };
    out.sort_by(|a, b| a.idx.cmp(&b.idx));
    out
}

fn create_trace_run_dir(base: &Path) -> Result<PathBuf, String> {
    let run_id = format!(
        "run-{}",
        Utc::now()
            .format("%Y%m%dT%H%M%S%.fZ")
            .to_string()
            .replace(':', "")
    );
    let run_root = base.join(run_id);
    fs::create_dir_all(run_root.join("projects")).map_err(|err| {
        format!(
            "failed to create trace directory {}: {err}",
            run_root.display()
        )
    })?;
    Ok(run_root)
}

fn write_trace_file(trace_root: Option<&Path>, name: &str, value: Value, verbose: bool) {
    let Some(root) = trace_root else {
        return;
    };
    let path = root.join(name);
    if let Err(err) = write_json(&path, &value) {
        if verbose {
            eprintln!("[warn] failed writing trace {}: {}", path.display(), err);
        }
    }
}

fn collect_summary_files(shadow_root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![shadow_root.to_path_buf()];

    while let Some(root) = stack.pop() {
        let Ok(entries) = fs::read_dir(&root) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(ft) = entry.file_type() else {
                continue;
            };
            if ft.is_dir() {
                stack.push(path);
            } else if ft.is_file()
                && path.file_name().and_then(|s| s.to_str()) == Some("summary.json")
            {
                out.push(path);
            }
        }
    }

    out.sort();
    out
}

fn hash_text(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

fn string_array_from_object(obj: &Map<String, Value>, key: &str) -> Vec<String> {
    obj.get(key)
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn build_embedding_text(obj: &Map<String, Value>) -> String {
    let name = obj
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("Unnamed");
    let description = obj
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("No description available");
    let category = obj
        .get("category")
        .and_then(|v| v.as_str())
        .unwrap_or("Exploratory");
    let maturity = obj
        .get("maturity")
        .and_then(|v| v.as_str())
        .unwrap_or("exploratory");

    let tags = string_array_from_object(obj, "tags");
    let concepts = string_array_from_object(obj, "concepts");
    let languages = string_array_from_object(obj, "languages");

    let mut lines = vec![
        format!("Project: {name}"),
        format!("Category: {category}"),
        format!("Maturity: {maturity}"),
        format!("Description: {description}"),
    ];

    if !languages.is_empty() {
        lines.push(format!("Languages: {}", languages.join(", ")));
    }
    if !tags.is_empty() {
        lines.push(format!("Tags: {}", tags.join(", ")));
    }
    if !concepts.is_empty() {
        lines.push(format!("Concepts: {}", concepts.join(", ")));
    }

    lines.join("\n")
}

fn parse_embedding_array(value: &Value) -> Option<Vec<f64>> {
    let arr = value.as_array()?;
    let mut out = Vec::with_capacity(arr.len());
    for item in arr {
        if let Some(v) = item.as_f64() {
            out.push(v);
        } else if let Some(v) = item.as_i64() {
            out.push(v as f64);
        } else {
            return None;
        }
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

fn extract_embedding_from_response(response: &Value) -> Option<Vec<f64>> {
    if let Some(vector) = parse_embedding_array(response) {
        return Some(vector);
    }

    if let Some(obj) = response.as_object() {
        if let Some(v) = obj.get("embedding") {
            if let Some(vector) = parse_embedding_array(v) {
                return Some(vector);
            }
        }

        if let Some(v) = obj.get("embeddings") {
            if let Some(items) = v.as_array() {
                if let Some(first) = items.first() {
                    if let Some(vector) = parse_embedding_array(first) {
                        return Some(vector);
                    }
                }
            }
        }

        if let Some(v) = obj.get("data") {
            if let Some(data_items) = v.as_array() {
                if let Some(first) = data_items.first() {
                    if let Some(embedding) = first.get("embedding") {
                        if let Some(vector) = parse_embedding_array(embedding) {
                            return Some(vector);
                        }
                    }
                }
            }
        }
    }

    None
}

fn parse_isq_type(value: &str) -> Result<IsqType, String> {
    let normalized = value
        .trim()
        .to_ascii_uppercase()
        .replace('-', "_")
        .replace('.', "_");
    match normalized.as_str() {
        "Q4_0" => Ok(IsqType::Q4_0),
        "Q4_1" => Ok(IsqType::Q4_1),
        "Q4_K" | "Q4_K_M" | "Q4_K_S" => Ok(IsqType::Q4K),
        "Q5_0" => Ok(IsqType::Q5_0),
        "Q5_1" => Ok(IsqType::Q5_1),
        "Q5_K" | "Q5_K_M" | "Q5_K_S" => Ok(IsqType::Q5K),
        "Q6_K" => Ok(IsqType::Q6K),
        "Q8_0" => Ok(IsqType::Q8_0),
        "Q8_K" => Ok(IsqType::Q8K),
        "Q2_K" => Ok(IsqType::Q2K),
        "Q3_K" | "Q3_K_M" | "Q3_K_S" => Ok(IsqType::Q3K),
        _ => Err(format!("unsupported --embed-local-isq value: {value}")),
    }
}

struct LocalEmbeddingEngine {
    runtime: tokio::runtime::Runtime,
    model: MistralModel,
}

impl LocalEmbeddingEngine {
    fn new(cfg: &LocalEmbeddingConfig, verbose: bool) -> Result<Self, String> {
        let isq = cfg
            .isq
            .as_ref()
            .map(|value| parse_isq_type(value))
            .transpose()?;

        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|err| format!("failed to build tokio runtime for local embeddings: {err}"))?;

        let model_id = cfg.model_id.clone();
        let hf_cache_path = cfg.hf_cache_path.clone();
        let force_cpu = cfg.force_cpu;
        let model = runtime
            .block_on(async move {
                let mut builder = EmbeddingModelBuilder::new(model_id);
                if let Some(isq_type) = isq {
                    builder = builder.with_isq(isq_type);
                }
                if force_cpu {
                    builder = builder.with_force_cpu();
                }
                if let Some(path) = hf_cache_path {
                    builder = builder.from_hf_cache_path(path);
                }
                if verbose {
                    builder = builder.with_logging();
                }
                builder.build().await
            })
            .map_err(|err| format!("failed to load local embedding model via mistral.rs: {err}"))?;

        Ok(Self { runtime, model })
    }

    fn embed(&self, text: &str) -> Result<Vec<f64>, String> {
        let prompt = text.to_string();
        let vector = self
            .runtime
            .block_on(async { self.model.generate_embedding(prompt).await })
            .map_err(|err| format!("local embedding inference failed: {err}"))?;
        Ok(vector.into_iter().map(|v| v as f64).collect())
    }
}

fn embedding_backend_key(cfg: &EmbeddingConfig) -> String {
    match &cfg.backend {
        EmbeddingBackend::Http { url } => url.clone(),
        EmbeddingBackend::Local(local) => {
            let suffix = local
                .isq
                .as_ref()
                .map(|value| format!("?isq={value}"))
                .unwrap_or_default();
            format!("mistralrs://{}{}", local.model_id, suffix)
        }
    }
}

fn embedding_backend_kind(cfg: &EmbeddingConfig) -> &'static str {
    match &cfg.backend {
        EmbeddingBackend::Http { .. } => "http",
        EmbeddingBackend::Local(_) => "mistralrs-local",
    }
}

fn build_embedding_input(cfg: &EmbeddingConfig, text: &str) -> String {
    format!("Instruct: {}\nQuery: {}", cfg.instruction, text)
}

fn fetch_embedding_http(
    cfg: &EmbeddingConfig,
    url: &str,
    embed_input: &str,
    verbose: bool,
) -> Option<Vec<f64>> {
    let payload = if url.contains("/v1/embeddings") {
        json!({
            "model": cfg.model,
            "input": [embed_input],
        })
    } else if url.contains("/api/embeddings") {
        json!({
            "model": cfg.model,
            "prompt": embed_input,
        })
    } else if url.contains("/api/embed") {
        json!({
            "model": cfg.model,
            "input": embed_input,
        })
    } else {
        json!({
            "inputs": embed_input,
            "normalize": true,
            "truncate": true,
        })
    };

    let body = payload.to_string();
    let output = Command::new("curl")
        .arg("-sS")
        .arg("--max-time")
        .arg(cfg.timeout_secs.to_string())
        .arg("-H")
        .arg("Content-Type: application/json")
        .arg("-X")
        .arg("POST")
        .arg(url)
        .arg("-d")
        .arg(body)
        .output();

    let Ok(result) = output else {
        if verbose {
            eprintln!("[warn] curl not available, cannot fetch embeddings");
        }
        return None;
    };

    if !result.status.success() {
        if verbose {
            let stderr = String::from_utf8_lossy(&result.stderr);
            eprintln!(
                "[warn] embedding request failed: {}",
                truncate_chars(stderr.trim(), 300)
            );
        }
        return None;
    }

    let stdout = String::from_utf8_lossy(&result.stdout);
    let Ok(parsed) = serde_json::from_str::<Value>(stdout.trim()) else {
        if verbose {
            eprintln!("[warn] embedding endpoint returned non-JSON payload");
        }
        return None;
    };

    extract_embedding_from_response(&parsed)
}

fn fetch_embedding(
    cfg: &EmbeddingConfig,
    text: &str,
    verbose: bool,
    local_engine: Option<&LocalEmbeddingEngine>,
) -> Option<Vec<f64>> {
    let embed_input = build_embedding_input(cfg, text);
    match &cfg.backend {
        EmbeddingBackend::Http { url } => fetch_embedding_http(cfg, url, &embed_input, verbose),
        EmbeddingBackend::Local(_) => {
            let Some(engine) = local_engine else {
                if verbose {
                    eprintln!("[warn] local embedding model is unavailable");
                }
                return None;
            };
            match engine.embed(&embed_input) {
                Ok(vector) => Some(vector),
                Err(err) => {
                    if verbose {
                        eprintln!("[warn] {err}");
                    }
                    None
                }
            }
        }
    }
}

fn cosine_similarity(a: &[f64], b: &[f64]) -> Option<f64> {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return None;
    }

    let mut dot = 0.0f64;
    let mut na = 0.0f64;
    let mut nb = 0.0f64;

    for idx in 0..a.len() {
        dot += a[idx] * b[idx];
        na += a[idx] * a[idx];
        nb += b[idx] * b[idx];
    }

    if na <= f64::EPSILON || nb <= f64::EPSILON {
        return None;
    }

    Some(dot / (na.sqrt() * nb.sqrt()))
}

fn merge_concepts(existing: Vec<String>, mined: &[String], max: usize) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();

    for item in existing.into_iter().chain(mined.iter().cloned()) {
        let trimmed = item.trim();
        if trimmed.is_empty() {
            continue;
        }
        let key = trimmed.to_lowercase();
        if seen.insert(key) {
            out.push(trimmed.to_string());
            if out.len() >= max {
                break;
            }
        }
    }

    out
}

fn ensure_object(value: &mut Value) -> Option<&mut Map<String, Value>> {
    value.as_object_mut()
}

fn current_embedding(obj: &Map<String, Value>) -> Option<Vec<f64>> {
    obj.get("_embedding")
        .and_then(|v| v.as_object())
        .and_then(|meta| meta.get("vector"))
        .and_then(parse_embedding_array)
}

fn update_embeddings_and_motifs(
    shadow_root: &Path,
    cfg: &EmbeddingConfig,
    dry_run: bool,
    verbose: bool,
    trace_root: Option<&Path>,
) -> (usize, usize) {
    let summary_files = collect_summary_files(shadow_root);
    if summary_files.is_empty() {
        return (0, 0);
    }

    let mut records: Vec<SummaryRecord> = Vec::new();
    let mut embedded_updates = 0usize;
    let mut embedding_events: Vec<Value> = Vec::new();
    let mut motif_events: Vec<Value> = Vec::new();
    let backend_key = embedding_backend_key(cfg);
    let backend_kind = embedding_backend_kind(cfg);

    let (local_engine, local_engine_error) = if dry_run {
        (None, None)
    } else {
        match &cfg.backend {
            EmbeddingBackend::Local(local_cfg) => {
                match LocalEmbeddingEngine::new(local_cfg, verbose) {
                    Ok(engine) => (Some(engine), None),
                    Err(err) => {
                        if verbose {
                            eprintln!("[warn] {err}");
                        }
                        (None, Some(err))
                    }
                }
            }
            EmbeddingBackend::Http { .. } => (None, None),
        }
    };

    for summary_path in summary_files {
        let Ok(text) = fs::read_to_string(&summary_path) else {
            continue;
        };
        let Ok(mut root) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let Some(obj) = ensure_object(&mut root) else {
            continue;
        };

        let embed_text = build_embedding_text(obj);
        let text_hash = hash_text(&embed_text);

        let mut should_fetch = true;
        if let Some(meta) = obj.get("_embedding").and_then(|v| v.as_object()) {
            let model_ok = meta.get("model").and_then(|v| v.as_str()) == Some(cfg.model.as_str());
            let backend_ok = meta
                .get("backend")
                .or_else(|| meta.get("backend_url"))
                .and_then(|v| v.as_str())
                == Some(backend_key.as_str());
            let hash_ok =
                meta.get("text_hash").and_then(|v| v.as_str()) == Some(text_hash.as_str());
            if model_ok && backend_ok && hash_ok {
                should_fetch = false;
            }
        }

        let path_value = obj
            .get("_path")
            .and_then(|v| v.as_str())
            .map(|v| v.to_string())
            .unwrap_or_else(|| summary_path.to_string_lossy().to_string());
        let mut changed = false;
        if should_fetch {
            if dry_run {
                embedded_updates += 1;
                embedding_events.push(json!({
                    "path": path_value,
                    "status": "would_update",
                    "reason": "dry_run",
                    "model": cfg.model,
                    "backend": backend_key.clone(),
                    "backend_kind": backend_kind,
                    "text_hash": text_hash,
                }));
            } else if let Some(vector) =
                fetch_embedding(cfg, &embed_text, verbose, local_engine.as_ref())
            {
                let dim = vector.len();
                obj.insert(
                    "_embedding".to_string(),
                    json!({
                        "model": cfg.model,
                        "backend": backend_key.clone(),
                        "backend_kind": backend_kind,
                        "backend_url": backend_key.clone(),
                        "instruction": cfg.instruction,
                        "text_hash": text_hash,
                        "normalized": true,
                        "dim": dim,
                        "updated_at": Utc::now().to_rfc3339(),
                        "vector": vector,
                    }),
                );
                changed = true;
                embedded_updates += 1;
                embedding_events.push(json!({
                    "path": path_value,
                    "status": "updated",
                    "model": cfg.model,
                    "backend": backend_key.clone(),
                    "backend_kind": backend_kind,
                    "text_hash": text_hash,
                    "dim": dim,
                }));
            } else {
                if verbose {
                    eprintln!(
                        "[warn] embedding fetch failed for {}",
                        summary_path.display()
                    );
                }
                let reason = local_engine_error
                    .clone()
                    .unwrap_or_else(|| "embedding_fetch_failed".to_string());
                embedding_events.push(json!({
                    "path": path_value,
                    "status": "failed",
                    "reason": reason,
                    "model": cfg.model,
                    "backend": backend_key.clone(),
                    "backend_kind": backend_kind,
                    "text_hash": text_hash,
                }));
            }
        } else {
            let dim = obj
                .get("_embedding")
                .and_then(|v| v.as_object())
                .and_then(|meta| meta.get("dim"))
                .and_then(|v| v.as_u64());
            embedding_events.push(json!({
                "path": path_value,
                "status": "cached",
                "model": cfg.model,
                "backend": backend_key.clone(),
                "backend_kind": backend_kind,
                "text_hash": text_hash,
                "dim": dim,
            }));
        }

        records.push(SummaryRecord {
            path: summary_path,
            data: obj.clone(),
            embedding: current_embedding(obj),
            changed,
        });
    }

    let mut motif_updates = 0usize;

    for idx in 0..records.len() {
        let Some(anchor_embedding) = records[idx].embedding.as_ref() else {
            continue;
        };

        let mut neighbors: Vec<(usize, f64)> = Vec::new();
        for j in 0..records.len() {
            if idx == j {
                continue;
            }
            let Some(other_embedding) = records[j].embedding.as_ref() else {
                continue;
            };
            let Some(similarity) = cosine_similarity(anchor_embedding, other_embedding) else {
                continue;
            };
            if similarity >= cfg.motif_min_sim {
                neighbors.push((j, similarity));
            }
        }

        neighbors.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(Ordering::Equal));
        neighbors.truncate(cfg.motif_neighbors);

        let own_terms: HashSet<String> = string_array_from_object(&records[idx].data, "concepts")
            .into_iter()
            .chain(string_array_from_object(&records[idx].data, "tags"))
            .map(|s| s.to_lowercase())
            .collect();

        let mut candidate_scores: HashMap<String, f64> = HashMap::new();
        let mut neighbor_links: Vec<Value> = Vec::new();

        for (neighbor_idx, sim) in neighbors {
            let neighbor = &records[neighbor_idx].data;
            let terms = string_array_from_object(neighbor, "concepts")
                .into_iter()
                .chain(string_array_from_object(neighbor, "tags"));

            for term in terms {
                let trimmed = term.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let key = trimmed.to_lowercase();
                if own_terms.contains(&key) {
                    continue;
                }
                *candidate_scores.entry(trimmed.to_string()).or_insert(0.0) += sim;
            }

            let neighbor_name = neighbor
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("Unnamed");
            let neighbor_path = neighbor.get("_path").and_then(|v| v.as_str()).unwrap_or("");
            neighbor_links.push(json!({
                "name": neighbor_name,
                "path": neighbor_path,
                "similarity": (sim * 1000.0).round() / 1000.0,
            }));
        }

        let mut candidates: Vec<(String, f64)> = candidate_scores.into_iter().collect();
        candidates.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap_or(Ordering::Equal)
                .then_with(|| a.0.cmp(&b.0))
        });

        let mined: Vec<String> = candidates
            .into_iter()
            .take(MAX_MINED_MOTIFS)
            .map(|(term, _)| term)
            .collect();
        if mined.is_empty() {
            continue;
        }

        let existing_concepts = string_array_from_object(&records[idx].data, "concepts");
        let merged = merge_concepts(existing_concepts.clone(), &mined, MAX_SUMMARY_CONCEPTS);

        if merged != existing_concepts {
            let project_path = records[idx]
                .data
                .get("_path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            records[idx].data.insert(
                "concepts".to_string(),
                Value::Array(merged.into_iter().map(Value::String).collect()),
            );
            records[idx].data.insert(
                "mined_motifs".to_string(),
                Value::Array(mined.into_iter().map(Value::String).collect()),
            );
            records[idx]
                .data
                .insert("_motif_neighbors".to_string(), Value::Array(neighbor_links));
            records[idx].data.insert(
                "_updated_at".to_string(),
                Value::String(Utc::now().to_rfc3339()),
            );
            records[idx].changed = true;
            motif_updates += 1;
            motif_events.push(json!({
                "path": project_path,
                "status": "updated",
                "motif_count": records[idx]
                    .data
                    .get("mined_motifs")
                    .and_then(|v| v.as_array())
                    .map(|v| v.len())
                    .unwrap_or(0),
                "neighbor_count": records[idx]
                    .data
                    .get("_motif_neighbors")
                    .and_then(|v| v.as_array())
                    .map(|v| v.len())
                    .unwrap_or(0),
                "motifs": records[idx]
                    .data
                    .get("mined_motifs")
                    .cloned()
                    .unwrap_or_else(|| Value::Array(Vec::new())),
                "neighbors": records[idx]
                    .data
                    .get("_motif_neighbors")
                    .cloned()
                    .unwrap_or_else(|| Value::Array(Vec::new())),
            }));
        }
    }

    if !dry_run {
        for record in records {
            if !record.changed {
                continue;
            }
            let root = Value::Object(record.data);
            if let Err(err) = write_json(&record.path, &root) {
                eprintln!("[warn] failed writing {}: {}", record.path.display(), err);
            }
        }
    }

    write_trace_file(
        trace_root,
        "embeddings.json",
        json!({
            "generated_at": Utc::now().to_rfc3339(),
            "model": cfg.model,
            "backend": embedding_backend_key(cfg),
            "backend_kind": embedding_backend_kind(cfg),
            "events": embedding_events,
        }),
        verbose,
    );
    write_trace_file(
        trace_root,
        "motifs.json",
        json!({
            "generated_at": Utc::now().to_rfc3339(),
            "updates": motif_updates,
            "events": motif_events,
        }),
        verbose,
    );

    (embedded_updates, motif_updates)
}

fn main() -> Result<(), String> {
    let run_started_at = Utc::now().to_rfc3339();
    let run_started = Instant::now();
    let home = env::var("HOME")
        .map(PathBuf::from)
        .map_err(|_| "HOME environment variable is not set".to_string())?;

    let cfg = parse_args(&home)?;

    if cfg.targets.is_empty() {
        return Err("No valid target directories to scan".to_string());
    }

    if !cfg.dry_run {
        fs::create_dir_all(&cfg.shadow_root).map_err(|err| {
            format!(
                "Failed to create shadow root {}: {err}",
                cfg.shadow_root.display()
            )
        })?;
    }

    let trace_root = if let Some(base) = &cfg.trace_dir {
        Some(create_trace_run_dir(base)?)
    } else {
        None
    };

    let gh_root = home.join("dev").join("gh");

    let mut project_roots: Vec<PathBuf> = Vec::new();
    for target in &cfg.targets {
        project_roots.extend(iter_project_roots(target, &gh_root));
    }

    let mut dedup = BTreeSet::new();
    project_roots.retain(|path| dedup.insert(path.clone()));

    if cfg.limit > 0 && project_roots.len() > cfg.limit {
        project_roots.truncate(cfg.limit);
    }

    println!("Summarization root: {}", cfg.shadow_root.display());
    println!(
        "Workers: {}",
        cfg.workers.max(1).min(project_roots.len().max(1))
    );
    println!(
        "Targets: {}",
        cfg.targets
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(", ")
    );
    println!("Discovered projects: {}", project_roots.len());
    if let Some(embed_cfg) = &cfg.embeddings {
        match &embed_cfg.backend {
            EmbeddingBackend::Http { url } => {
                println!(
                    "Embeddings: enabled (http={}, model={})",
                    url, embed_cfg.model
                );
            }
            EmbeddingBackend::Local(local_cfg) => {
                let isq = local_cfg.isq.as_ref().map(|v| v.as_str()).unwrap_or("auto");
                println!(
                    "Embeddings: enabled (mistralrs-local model={} isq={} force_cpu={})",
                    local_cfg.model_id, isq, local_cfg.force_cpu
                );
            }
        }
    } else {
        println!("Embeddings: disabled (set --embed-local-model or --embed-url to enable)");
    }
    if let Some(trace_root) = &trace_root {
        println!("Trace directory: {}", trace_root.display());
    }

    let outcomes = process_projects(&project_roots, &home, &cfg, trace_root.as_deref());
    let mut stats: HashMap<String, usize> = HashMap::new();
    let mut errors: Vec<Value> = Vec::new();

    for outcome in &outcomes {
        *stats.entry(outcome.status.clone()).or_insert(0) += 1;
        if outcome.idx == usize::MAX {
            println!(
                "[worker] {:>11}  {}",
                outcome.status,
                outcome.path.display()
            );
        } else {
            println!(
                "[{}/{}] {:>11}  {}",
                outcome.idx + 1,
                project_roots.len(),
                outcome.status,
                outcome.path.display()
            );
        }

        if let Some(err) = &outcome.error {
            if cfg.verbose {
                eprintln!(
                    "[warn] project processing failed for {}: {}",
                    outcome.path.display(),
                    err
                );
            }
            errors.push(json!({
                "index": outcome.idx,
                "path": outcome.path.to_string_lossy(),
                "error": err,
            }));
        }
    }

    let mut embedded_updates = 0usize;
    let mut motif_updates = 0usize;

    if let Some(embed_cfg) = &cfg.embeddings {
        let (embeds, motifs) = update_embeddings_and_motifs(
            &cfg.shadow_root,
            embed_cfg,
            cfg.dry_run,
            cfg.verbose,
            trace_root.as_deref(),
        );
        embedded_updates = embeds;
        motif_updates = motifs;
        println!(
            "Embedding enrichment: embeddings_updated={}, motif_enriched={}",
            embedded_updates, motif_updates
        );
    }

    println!("Done.");
    let summary_parts = ["updated", "would_update", "skip", "error"]
        .iter()
        .filter_map(|key| stats.get(*key).map(|count| format!("{}={}", key, count)))
        .collect::<Vec<_>>();
    if !summary_parts.is_empty() {
        println!("Summary: {}", summary_parts.join(", "));
    }
    if embedded_updates > 0 || motif_updates > 0 {
        println!(
            "Embedding Summary: embeddings_updated={}, motif_enriched={}",
            embedded_updates, motif_updates
        );
    }

    write_trace_file(
        trace_root.as_deref(),
        "run.json",
        json!({
            "started_at": run_started_at,
            "finished_at": Utc::now().to_rfc3339(),
            "elapsed_ms": run_started.elapsed().as_millis(),
            "shadow_root": cfg.shadow_root.to_string_lossy(),
            "targets": cfg.targets.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
            "workers": cfg.workers,
            "project_count": project_roots.len(),
            "stats": stats,
            "embedding_updates": embedded_updates,
            "motif_updates": motif_updates,
            "errors": errors,
        }),
        cfg.verbose,
    );

    if !errors.is_empty() {
        return Err(format!(
            "{} project(s) failed during processing",
            errors.len()
        ));
    }

    Ok(())
}
