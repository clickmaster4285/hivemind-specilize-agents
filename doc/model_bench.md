# Model comparison  (runs/prompt = 2, judge = `qwen3.5:9b`)

## 1. Diagrams — real render validation + quality

| Model | block% | raw-valid% | valid-after-sanitize% | avg nodes | emoji% | classDef% | clutter% | avg s | GPU/CPU |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 100 | 100 | 100 | 6.2 | 89 | 0 | 0 | 21.8 | 100% GPU |
| `qwen2.5-coder:7b` | 100 | 100 | 100 | 5.9 | 70 | 0 | 0 | 13.3 | 100% GPU |
| `qwen2.5-coder:14b` | 100 | 100 | 100 | 5.8 | 70 | 0 | 0 | 74.4 | 41%/59% CPU/GPU |

## 2. Answer quality — judge 1-5 (overall + by language)

| Model | correct | structure | clarity | complete | overall | EN overall | HI overall | judged% |
|---|---|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 5.0 | 5.0 | 5.0 | 5.0 | 5.00 | 5.00 | 5.00 | 100 |
| `qwen2.5-coder:7b` | 4.6 | 4.9 | 4.9 | 4.1 | 4.61 | 4.88 | 4.25 | 88 |
| `qwen2.5-coder:14b` | 4.9 | 5.0 | 5.0 | 4.6 | 4.88 | 4.94 | 4.81 | 100 |

## 3. Consistency — per-diagram-prompt render pass-rate (valid-after-sanitize)

| Model | office-net | web-arch | boot-flow | vlan-seg | hinglish-net | all-pass% |
|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 100 |
| `qwen2.5-coder:7b` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 100 |
| `qwen2.5-coder:14b` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 100 |

## 4. Structured JSON success rate + latency spread

| Model | options | quiz | suggestions | clarify | struct avg s | diagram min/med/max s |
|---|---|---|---|---|---|---|
| `qwen3.5:9b` | 100% | 100% | 100% | 25% | 21.3 | 12/24/35 |
| `qwen2.5-coder:7b` | 100% | 100% | 100% | 25% | 12.5 | 5/14/27 |
| `qwen2.5-coder:14b` | 100% | 100% | 100% | 25% | 62.4 | 32/72/130 |

## 5. Instruction-adherence on diagrams (higher = follows the rules)

| Model | <=7 nodes% | emoji-on-nodes% | no-classDef% | no-IP-in-node% |
|---|---|---|---|---|
| `qwen3.5:9b` | 90 | 89 | 100 | 100 |
| `qwen2.5-coder:7b` | 90 | 70 | 100 | 100 |
| `qwen2.5-coder:14b` | 100 | 70 | 100 | 100 |

## Diagram samples (first block per prompt — paste into mermaid.live to eyeball)

### `qwen3.5:9b`

**office-net**

```mermaid
flowchart TD
    PC1["💻 PC 1"] --> SW["🔀 Switch"]
    PC2["💻 PC 2"] --> SW
    SRV["🖥️ File Server"] --> SW
    SW --> FW["🛡️ Router/Firewall"]
    FW --> NET["🌐 Internet"]
```

**web-arch**

```mermaid
flowchart TD
  Client["🌐 Browser"] -->|HTTPS| NGINX["🛡️ Nginx Reverse Proxy"]
  NGINX -->|HTTP/API Calls| API["⚙️ API Service"]
  API -->|SQL Queries| PG["🗄️ PostgreSQL Database"]
```

**boot-flow**

```mermaid
flowchart TD
  Power["⚡ Power On"] --> BIOS["🖥️ BIOS/UEFI POST Check"]
  BIOS --> Device["🔍 Find Boot Device"]
  Device --> Bootloader["📜 Load Bootloader"]
  Bootloader --> Kernel["⚙️ Load OS Kernel"]
  Kernel --> Drivers["🔌 Initialize Drivers"]
  Drivers --> Desktop["🎨 Show Desktop/Login"]
```

**vlan-seg**

```mermaid
flowchart TD
    PC1["💻 PC 1 (VLAN 10)"] --> SW["🔀 Switch"]
    PC2["💻 PC 2 (VLAN 20)"] --> SW
    PC3["💻 PC 3 (VLAN 30)"] --> SW
    SRV["🖥️ Server (VLAN 10)"] --> SW
    
    SW --> FW["🛡️ Router/Firewall"]
    
    FW --> NET["🌐 Internet"]
```

**hinglish-net**

```mermaid
flowchart TD
    PC1["💻 PC 1"] --> SW["🔀 Switch"]
    PC2["💻 PC 2"] --> SW
    SRV["🖥️ File Server"] --> SW
    SW --> FW["🛡️ Router/Firewall"]
    FW --> NET["🌐 Internet"]
```

### `qwen2.5-coder:7b`

**office-net**

```mermaid
flowchart TD
  PC1["💻 PC 1"] --> SW["🔀 Switch"]
  PC2["💻 PC 2"] --> SW
  SRV["🖥️ Server"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```

**web-arch**

```mermaid
flowchart TD
  W["💻 Web Browser"] --> N["🌐 Nginx"]
  N --> A["💻 API Service"]
  A --> D["🔒 Postgres Database"]
  W -->|"HTTPS/HTTP"| N
  N -->|"Reverse Proxy"| A
  A -->|"Database Queries"| D
```

**boot-flow**

```mermaid
flowchart TD
  P["Power-On"] --> POST["POST"]
  POST --> B["Loading Bootloader"]
  B --> OS["Loading Operating System"]
  OS --> D["Initializing Drivers"]
  D --> S["Loading System Services"]
  S --> T["Displaying Desktop"]
```

**vlan-seg**

```mermaid
flowchart TD
  VLAN1["vlan 10"] --> SW["🖥️ Switch"]
  VLAN2["vlan 20"] --> SW
  VLAN3["vlan 30"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```

**hinglish-net**

```mermaid
flowchart TD
  PC1["💻 PC 1"] --> SW["🔀 Switch"]
  PC2["💻 PC 2"] --> SW
  SRV["🖥️ Server"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```

### `qwen2.5-coder:14b`

**office-net**

```mermaid
flowchart TD
  PC1["💻 PC 1"] --> SW["🔀 Switch"]
  PC2["💻 PC 2"] --> SW
  SRV["🖥️ File Server"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```

**web-arch**

```mermaid
flowchart TD
  WEB["🌐 Web Client"] --> NGINX["nginx"]
  NGINX --> API["API Service"]
  API --> DB["Postgres"]
```

**boot-flow**

```mermaid
flowchart TD
  POW["⚡ Power-On"] --> BIOS["⚙️ BIOS/UEFI Initialization"]
  BIOS --> BOOT["Boot Device Selection"]
  BOOT --> LOADER["Loading Boot Loader"]
  LOADER --> OS["Loading Operating System"]
  OS --> LOGIN["User Login"]
  LOGIN --> DESKTOP["Desktop Environment"]
```

**vlan-seg**

```mermaid
flowchart TD
  VLAN1["🌐 VLAN 1"] --> SW["🔀 Switch"]
  VLAN2["🌐 VLAN 2"] --> SW
  VLAN3["🌐 VLAN 3"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```

**hinglish-net**

```mermaid
flowchart TD
  PC1["💻 PC 1"] --> SW["🔀 Switch"]
  PC2["💻 PC 2"] --> SW
  SRV["🖥️ File Server"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```

