# Friends Image Studio

Self-hosted image generation and image cataloguing for a small friend group. It now has two sites: an image generator and a separate image library for organizing files into nested groups such as comic series, issues, and pages. It also includes optional Discord commands and configurable prompt rules.

## What this starter includes

- Text-to-image and image-to-image forms in the website, using Qwen-Image-2.1 or Rapid AIO v19.
- Prompt expansion with the existing Qwen PE workflows or the Heretic prompt-enhancer service.
- Select and delete individual images in the generator gallery and the image library.
- Admin-created friend accounts; generated images are private to each account.
- Admin controls to enable or disable each generation mode, set a prompt length limit, and block a list of words or phrases.
- Optional Discord commands `/imagine` and `/edit`, available to members of any server where you invite the bot; no server-ID whitelist is needed.
- ComfyUI and model files stay on the OpenStack instance. The ComfyUI port is only reachable on the private Docker network.
- Caddy terminates HTTPS for the website when the domain points to the instance.
- A separate React/TypeScript image-library site with an Apollo Server GraphQL API and PostgreSQL metadata database.
- Collections can be nested (for example, a series containing issues); images can be titled, described, reordered, removed from a group, or deleted.
- Image binaries are stored on the server's `media-files/` directory; Postgres stores collection structure and image metadata. Uploads accept PNG, JPEG, and WebP.
- The library site is protected by a shared HTTP Basic Auth login for your group. Postgres and the media API have no public host ports.

## Important limitation of the policy filter

The configurable rules inspect the text prompt and reject a matching word or phrase. They do **not** understand every synonym, infer the meaning of a prompt, or scan generated pixels for prohibited content. A prompt filter alone cannot guarantee what appears in an image. Use the service with people you trust; if you need meaning-based or output-image moderation, add a classifier and test its false positives and misses before sharing the service more broadly.

## OpenStack instance

Create an Ubuntu 24.04 instance with an NVIDIA GPU attached and enough persistent disk for the model, Docker images, output images, and backups. Use a GPU flavor supported by the model workflow you choose. Qwen Image Edit is a much larger model than SDXL; check the current model card and ComfyUI workflow for memory needs before selecting a flavor.

The default ComfyUI container base is `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime`. If you change it, set `PYTORCH_BASE` in `.env` before running the setup. The setup checks Docker GPU access using that same PyTorch image before downloading the large models.

In the OpenStack security group, allow:

- TCP 22 only from your own public IP (for SSH administration).
- TCP 80 and 443 from the people who need the site (or the internet if you want it reachable from anywhere).

Do not open TCP 8000 or 8188. The website API and ComfyUI are not published on host ports by Compose.

Install [Docker Engine for Ubuntu](https://docs.docker.com/engine/install/ubuntu/) and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) using their official installation guides. Verify `nvidia-smi` works on the host and that Docker can see the GPU before starting this stack.

## First start: automated setup

The setup uses these three files together: `scripts/setup.sh`, `scripts/setup.py`, and `scripts/model-manifest.json`. If they are not yet in your checkout, copy them from the supplied `image-studio-setup.zip` into the repository's `scripts/` directory first.

Prepare the host before running the setup:

- Install Docker Engine with the Compose plugin, the NVIDIA driver, and NVIDIA Container Toolkit.
- Install Python 3.9 or newer and `curl`. Verify `nvidia-smi` works on the host.
- Point two different DNS hostnames at the instance: one for the generator and one for the archive. Ports 80 and 443 must be reachable.
- Allow about **93 GB for model files**, plus space for Docker images, builds, generated images, and backups. The download check reserves another 20 GiB.
- Allow internet access to Hugging Face and the container/package registries used by the Dockerfiles.

Clone the project, run the setup once, then start the application:

```bash
git clone https://github.com/Ben-Joel-Schoenbein/image-generation-platform.git
cd image-generation-platform
sudo -v
bash scripts/setup.sh
sudo docker compose up -d
```

For an existing checkout, run the last three commands from its root directory.

The setup asks for the generator and archive domains, the generator admin password, the shared archive password, and an optional Discord bot token. Press Enter at a password prompt to generate a random password. Press Enter at the token prompt to leave Discord disabled. Newly configured login credentials are saved in `setup-credentials.txt` with permissions restricted to your user.

The setup:

- Creates a private `.env` from `.env.example`, preserves existing values, and fills missing or recognizable example credentials.
- Downloads the current Qwen-Image-2.1, Rapid AIO v19, and Heretic files to the model paths used by the workflows.
- Verifies file sizes and checksums; interrupted downloads resume from `.part` files.
- Copies the Heretic system prompts and licenses into `web/heretic_prompts/` for the web image build.
- Sets `COMPOSE_FILE` to include `compose.heretic.yaml` and enables the Discord profile when a token is configured.
- Builds the containers and pulls the required service images.

After a successful setup, the normal `sudo docker compose up -d` command includes Heretic and any configured Discord bot. The setup itself does not start or stop the application. Shell exports of `COMPOSE_FILE`, `COMPOSE_PROFILES`, or `COMPOSE_PATH_SEPARATOR` must not override the project configuration; the setup detects them and asks you to unset them.

Check the started services:

```bash
sudo docker compose ps
sudo docker compose logs --tail=100 web comfyui prompt-enhancer media-api media-db media-web caddy
```

Open `https://<generator-domain>`, sign in with the admin credentials, and create accounts in **Admin settings**. Open `https://<archive-domain>` with the shared archive login. Caddy obtains HTTPS certificates when DNS and the public ports are ready.

### Setup options and retries

Check only the checkout and required model names, without downloads or writes:

```bash
bash scripts/setup.sh --check
```

This offline check does not validate Docker, GPU access, or the complete server configuration.

For setup without interactive prompts, supply the real hostnames; passwords are generated and saved locally, and an unconfigured Discord bot stays disabled:

```bash
bash scripts/setup.sh --non-interactive \
  --domain images.your-domain.de \
  --library-domain archive.your-domain.de
```

To prepare models and configuration without building containers:

```bash
bash scripts/setup.sh --no-build
sudo docker compose up -d --build
```

Run the same setup command again after a download interruption. Verified complete files and existing credentials are reused. Files with different checksums and customized Heretic prompts are not overwritten. An existing `.env` is backed up before changes.

### Moving an existing installation

For a move that keeps accounts, settings, and pictures, restore the original `.env`, `data/`, `media-files/`, and a consistent PostgreSQL database backup on the destination. Copy `comfy-output/` if you want to keep the worker's outputs, and copy `models/` to avoid downloading them again. Git contains application code and workflows; account data and archive contents are stored outside Git.

The setup refuses to generate replacement admin/database credentials when it detects existing generator data or an archive database volume that needs the original configuration. It does not migrate or delete your databases.

## Image library and GraphQL

Open the library at `https://LIBRARY_DOMAIN`. Create a top-level group for a series, then make child groups for issues and upload page images into each issue. The sidebar shows the hierarchy. Use the arrow buttons to order pages. Deleting a group also deletes its child groups and images that belong only to those groups; images attached to another group are kept.

The frontend talks to Apollo Server at `https://LIBRARY_DOMAIN/graphql`. The API also has a multipart upload endpoint at `/upload` and serves protected image files under `/media/{id}`. Caddy requires the shared Basic Auth login and forwards a separate private header to the API; the API rejects requests that bypass the proxy. PostgreSQL is accessible only inside the Docker network. Generated images from the generator can be downloaded and uploaded to a library group.

The database creates its tables on first startup. No SQL setup is required. If you later want per-friend access, sharing links, image import from the generator, tags, or comic-reader navigation, those can be added to the GraphQL schema and UI.

## Current model workflows

Both `workflows/text2img.api.json` and `workflows/img2img.api.json` use Qwen-Image-2.1. Rapid AIO builds its workflow in `web/rapid_aio.py`. The setup installs:

| Purpose | Files |
| --- | --- |
| Qwen-Image-2.1 image generation/editing | `qwen_image_2.1_bf16.safetensors`, `qwen3vl_8b_bf16.safetensors`, `qwen_image_2.1_vae_bf16.safetensors` |
| Regular Qwen prompt expansion | `qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors`, `qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors` |
| Rapid AIO | `Qwen-Rapid-AIO-NSFW-v19.safetensors` |
| Heretic text prompt expansion | `pe_t2i_heretic-Q4_K_M.gguf`, `system_prompt.txt`, `LICENSE` |
| Heretic reference-image prompt expansion | `pe_i2i_heretic-Q4_K_M.gguf`, `pe_i2i_heretic.mmproj-bf16.gguf`, `system_prompt.txt`, `LICENSE` |

The manifest pins exact Hugging Face revisions and file checksums. For this setup, use `bash scripts/setup.sh`; the older `scripts/download-sdxl.sh` and `qwen-edit-setup/download-models.sh` target earlier workflows.

Edit mode accepts up to ten reference images. Mention “Image 1”, “Image 2”, etc. in the prompt. More references share the available reference resolution, so evaluate the result with your inputs. Standard upload limits are 15 MB per image and 60 MB total.

To use another workflow filename, edit `WORKFLOW_TEXT` or `WORKFLOW_EDIT` in `compose.yaml` and mount that file from `./workflows`. If you change model filenames, update the setup manifest too; it rejects workflow models that it does not cover.

## Optional Discord bot

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications), add a bot, and copy its token into `.env` as `DISCORD_TOKEN`.
2. Invite it to your server with the `bot` and `applications.commands` scopes. It only needs permission to view the channel, send messages, and attach files.
3. Invite the bot to the servers where it should be used. Commands are synchronized globally and work only in installed server contexts. No server or user IDs need to be configured.
4. Set `DISCORD_BOT_SECRET` to a second random secret (for example `openssl rand -hex 32`).
5. If you added the token after the initial setup, run `bash scripts/setup.sh` again. It preserves existing credentials and enables `COMPOSE_PROFILES=discord`. Then start the service with the normal command:

   ```bash
   sudo docker compose up -d
   ```

   To enable the profile for just one command instead, use `sudo docker compose --profile discord up -d --build`.

The commands are `/imagine prompt:...` and `/edit prompt:... reference:... reference2:...` through `reference10:...`. Only the first reference is required. Discord uses the same blocked phrases and mode toggles as the website. To stop just the bot, run `docker compose stop discord`.

## Policy and model administration

The admin page supports:

- One blocked word or phrase per line. Matching ignores case and punctuation differences.
- Turning text generation or reference-image editing off for everyone.
- A maximum prompt length, up to 2,000 characters.
- Creating separate sign-in accounts for each friend.

Model weights and ComfyUI workflows are system-level files. Only someone with SSH access to the instance can change them. Do not expose ComfyUI's own web interface, the media API, or PostgreSQL to the public internet. Only Caddy publishes host ports.

## Backups and maintenance

Back up `.env`, `data/`, `media-files/`, `models/`, and any custom workflows. Export the database metadata with `docker compose exec -T media-db pg_dump -U image_library image_library > image-library-backup.sql`. Generated images are stored in `data/images/`; ComfyUI also keeps intermediate outputs in `comfy-output/`. These directories grow over time, so periodically remove old files after taking any backups you want to keep. Update the project deliberately and test the model workflow and image upload after updates:

```bash
docker compose pull caddy
docker compose build --pull
docker compose up -d
```

## Layout

```text
compose.yaml             web, local model worker, HTTPS proxy, optional Discord bot
compose.heretic.yaml     Heretic prompt-enhancer service and web integration
scripts/setup.sh         automated setup entry point
scripts/setup.py         models, GPU check, private configuration, container builds
scripts/model-manifest.json pinned model sources, sizes, and checksums
web/app.py               account portal, policy checks, image gallery, internal bot API
discord/bot.py           Discord slash commands for installed servers
media-api/src/index.ts   Apollo Server, PostgreSQL, image upload and collection API
media-web/src/           React + TypeScript image-management website
media-files/             uploaded image binaries (created on first upload)
postgres_data             database volume for metadata and collection relations
workflows/               ComfyUI API workflow files
models/                  local model weights (not included)
data/                    SQLite accounts, rules, and generated images
```
