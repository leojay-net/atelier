# Vultr Agent Rush Hackathon — Progress & Strategy

**Event:** [Vultr: Agent Rush Hackathon (Lablab.ai)](https://lablab.ai/ai-hackathons/vultr-hackathon)  
**Timeline:** November 3 – November 8, 2026  
**Total Prizes:** $9,000 cash + $5,000 Vultr credits  
**Submission Portal:** Lablab.ai (Team: `leo Team`)  
**Project:** **Atelier** (`https://github.com/leojay-net/atelier`)

---

## 1. Hackathon Requirements vs Atelier Execution

| # | Hackathon Rule / Requirement | Atelier Implementation | Status |
|---|---|---|:---:|
| 1 | **Vultr VM Central Orchestration**<br>Backend must run on a Vultr VM as central control. | Docker Compose multi-service architecture (`api` ASGI server + `worker` media engines + `web` Next.js frontend). One-line deployment via `deploy/vultr_setup.sh`. | ✅ Ready to boot |
| 2 | **Vultr Serverless Inference for all LLM calls**<br>Must route all agent calls to `https://api.vultrinference.com/v1`. | Native `vultr` provider adapter implemented using OpenAI-compatible client, configured for `llama-3.3-70b-instruct` / `deepseek-v4.1-flash`. Unit tested and verified. | ✅ Complete |
| 3 | **Strict Sandbox Execution Boundary**<br>Tasks must run inside isolated containers/throwaway instances—never directly in main process. | Zero-mount Docker sandbox (`--network none`, `--read-only` root, `--cap-drop ALL`, tmpfs work/tmp, execution timeout). Untrusted media and commands are strictly isolated. | ✅ Complete |
| 4 | **Required "Containment Moment" in Demo Video**<br>Video must show the sandbox safely absorbing hostile actions (`rm -rf`, infinite loop, network attack). | Automated suite `scripts/demo_containment.py` executes: 1) Destructive `rm -rf /` attack absorbed by read-only root. 2) Runaway CPU loop killed by timeout guard. 3) Network exfiltration blocked by air-gapped network. | ✅ Complete |
| 5 | **Public GitHub Repository**<br>Open-source repo with documentation and setup guides. | Live at `https://github.com/leojay-net/atelier` with full architecture documentation, ADRs, and deployment guides. | ✅ Live |
| 6 | **Public Interactive Demo URL**<br>Real web experience for judges (not just a local video). | Next.js 16 UI in `web/` bound to port 3000 on the Vultr VM public IP, offering live drag-drop uploads, natural-language editing, and real-time SSE progress streaming. | ✅ Ready to deploy |
| 7 | **Maximizing Vultr Infrastructure (Bonus Advantage)**<br>Go beyond basic VM by leveraging full Vultr ecosystem. | Integrated **Vultr Object Storage** (`ewr1.vultrobjects.com`) for durable S3-compatible content-addressed storage of all media inputs and outputs, plus dynamic model discovery. | ✅ Integrated |
| 8 | **User Rules, Limitations & Negative Constraints**<br>Allow operators to inject strict rules, negative constraints, and bounds into agent execution. | Dynamic rules in BrandKit schemas, CLI flags (`--rules`, `--rules-file`), worker prompt injection, and interactive settings UI in Next.js web app. Verified with unit tests. | ✅ Complete |

---

## 2. Judging Rubric Maximization Plan

Submissions are evaluated on 4 criteria (25% each). Here is how we maximize each score:

### A. Application of Technology (25%)
* **Maximized Vultr Surface Area**: We don't just use a single VM—we connect **Vultr Cloud Compute**, **Vultr Serverless Inference**, **Vultr Object Storage**, and **Docker container boundaries**.
* **Zero-Mount Sandbox**: Rather than executing raw Python or shell commands on the host machine, Atelier treats model output as untrusted specs, passing data via isolated tar streams into disposable containers.

### B. Business Value (25%)
* **90% Token Reduction**: Unlike general-purpose coding agents that waste tens of thousands of tokens repeatedly generating raw ffmpeg commands, Atelier's model writes a 200-token declarative JSON spec. The engine handles the execution deterministically.
* **Production-Grade Media Engine**: Supports image editing (Pillow), video/audio rendering & loudness normalization (pinned ffmpeg filtergraphs), and document processing (Word, Excel, PowerPoint, PDF).

### C. Originality (25%)
* **"The model decides *what* to do. The engine decides *how*, identically every time."**
* Instead of brittle code generation, Atelier pairs an AI director with a deterministic media engine, offering cache hits at 0 tokens and predictable pixel-perfect results.

### D. Presentation (25%)
* **Live Product**: Judges can visit the public URL, upload an image or video, type a prompt, and see live progress via Server-Sent Events.
* **Clear Containment Moment**: Video demo includes a side-by-side terminal / UI demo showing an injected destructive script safely absorbed without crashing the server.

---

## 3. Step-by-Step Execution Plan

```
[Phase 1: Code & Integrations] (DONE)
  ├── Vultr Serverless Inference Adapter (OpenAI-compatible)
  ├── Docker Sandbox with containment guards
  ├── Containment verification script (scripts/demo_containment.py)
  ├── System verification test suite (scripts/verify_system.py - 6/6 stages passing)
  ├── Rules, Limitations & Restrictions (CLI, API, Worker, Next.js UI)
  ├── Vultr Object Storage integration (S3Backend)
  └── Multi-stage Next.js Dockerfile & docker-compose

[Phase 2: Cloud Deployment] (CURRENT STEP)
  ├── Spin up Ubuntu VM on Vultr ($200 credits)
  ├── Run deploy/vultr_setup.sh with VULTR_INFERENCE_API_KEY
  └── Verify Web UI at http://<PUBLIC_IP>:3000 and REST API at http://<PUBLIC_IP>:8000

[Phase 3: Demo & Submission] (Nov 6 - Nov 8)
  ├── Record 3-minute video:
  │     0:00 - 0:45: The Problem & Architecture (Vultr VM + Inference + Object Storage)
  │     0:45 - 1:45: Live Product Demo (Describe media edits -> live generation)
  │     1:45 - 2:30: The Containment Moment (Hostile command/loop neutralised)
  │     2:30 - 3:00: Business Impact & Wrap-up
  ├── Upload video to YouTube (Public/Unlisted)
  └── Complete Lablab.ai submission form before Nov 8 deadline
```
