<p align="center">
  <a href="https://pexafy.com/mcp/">
    <img src="https://raw.githubusercontent.com/Pexafy/pexafy-mcp/docs/readme-vitrine/docs/assets/readme-hero.png" width="100%" alt="Pexafy MCP server. Real photos, right in the chat: a grid of free-to-use photographs with their credits, and one opened large with buttons to like it, find more like it or download it.">
  </a>
</p>

<p align="center">
  <a href="https://insiders.vscode.dev/redirect/mcp/install?name=pexafy&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fmcp.pexafy.com%2Fmcp%22%7D"><img src="https://raw.githubusercontent.com/Pexafy/pexafy-mcp/docs/readme-vitrine/docs/assets/badge-vscode.png" height="46" alt="Install in VS Code"></a>
  <a href="https://chatgpt.com/plugins/plugin_asdk_app_6a837089eb1c8191b29a0d126a54f770?search=pexafy"><img src="https://raw.githubusercontent.com/Pexafy/pexafy-mcp/docs/readme-vitrine/docs/assets/badge-chatgpt.png" height="46" alt="Add to ChatGPT"></a>
  <a href="https://cursor.com/en/install-mcp?name=pexafy&config=eyJ1cmwiOiJodHRwczovL21jcC5wZXhhZnkuY29tL21jcCJ9"><img src="https://raw.githubusercontent.com/Pexafy/pexafy-mcp/docs/readme-vitrine/docs/assets/badge-cursor.png" height="46" alt="Add to Cursor"></a>
  <a href="https://pexafy.com/mcp/#setup"><img src="https://raw.githubusercontent.com/Pexafy/pexafy-mcp/docs/readme-vitrine/docs/assets/badge-claude.png" height="46" alt="Connect Claude"></a>
</p>

<h3 align="center">Ask for a photo the way you would describe it to a person.<br>Get real, free-to-use photographs back, inside the conversation, credits included.</h3>

<p align="center">
  <a href="https://github.com/Pexafy/pexafy-mcp/actions/workflows/ci.yml"><img src="https://github.com/Pexafy/pexafy-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://registry.modelcontextprotocol.io/v0.1/servers?search=com.pexafy/pexafy-mcp"><img src="https://img.shields.io/badge/MCP_Registry-com.pexafy%2Fpexafy--mcp-8b5cf6" alt="Official MCP Registry"></a>
  <a href="https://github.com/Pexafy/pexafy-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e" alt="MIT licence"></a>
</p>

<br>

## Describe it. Choose it. Use it.

<table>
<tr>
<td width="33%" valign="top">

### 🗣️ Describe

One sentence, any language: *“someone working alone late at night, lit only by a warm desk lamp”*. Pexafy matches the **scene**, not tags.

</td>
<td width="33%" valign="top">

### ❤️ Choose

Sixteen photographs land in a grid **inside the chat**. Open one large, swipe, heart the keepers, press **≈** for more like it.

</td>
<td width="33%" valign="top">

### 📎 Use

Your picks go back to the assistant **in your order**, with the licence and the credit line to display, or as an **image file** for a slide, a post or a doc.

</td>
</tr>
</table>

## Add it in 30 seconds

| | |
|---|---|
| **VS Code** | [Install in VS Code](https://insiders.vscode.dev/redirect/mcp/install?name=pexafy&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fmcp.pexafy.com%2Fmcp%22%7D), or `code --add-mcp '{"name":"pexafy","type":"http","url":"https://mcp.pexafy.com/mcp"}'` |
| **ChatGPT** | [Add Pexafy](https://chatgpt.com/plugins/plugin_asdk_app_6a837089eb1c8191b29a0d126a54f770?search=pexafy) from ChatGPT's plugin directory |
| **Claude** | *Customize → Connectors → Add custom connector*, then `https://mcp.pexafy.com/mcp` |
| **Claude Code** | `claude mcp add --transport http pexafy https://mcp.pexafy.com/mcp` |
| **Cursor** | [Add to Cursor](https://cursor.com/en/install-mcp?name=pexafy&config=eyJ1cmwiOiJodHRwczovL21jcC5wZXhhZnkuY29tL21jcCJ9) |
| **Any MCP client** | Streamable HTTP at `https://mcp.pexafy.com/mcp` |

**Nothing to install, no API key to paste.** In VS Code, Cursor and other editors you can search right away, without an account. ChatGPT and Claude ask you to sign in to Pexafy once; a free account is enough.

## Five tools, all read-only

| Tool | What it does |
|---|---|
| `search_photos` | Photographs described in one sentence, in any language, optionally only landscape, portrait or square. |
| `search_photos_by_image` | Photographs that look like a picture (a link, an upload) or like a Pexafy photo: *“like this, but at night”*. |
| `get_photo_file_by_photo_id` | One photograph as an image file, up to 1280 px wide, with its credit line. |
| `get_grid_selected_photos` | The photographs you liked in the grid, in the order you chose. |
| `connect_account` | Links your Pexafy account, right from the conversation. |

Where the host renders MCP Apps (ChatGPT, Claude, VS Code), results arrive as the grid above. Everywhere else, each one comes back as a link with its credit line.

## Where the photos come from

Pexafy searches **its own index** of millions of free-to-use photographs, collected from stock libraries such as Unsplash, Pexels and Pixabay. Every photograph is indexed by meaning, so a whole scene in one sentence beats a list of keywords. Each result names its source and licence and carries the credit line to display.

<br>

<p align="center">
  <a href="https://pexafy.com/mcp/"><b>pexafy.com/mcp</b></a> &nbsp;·&nbsp;
  <a href="https://github.com/Pexafy/pexafy-mcp/blob/main/DEVELOPMENT.md">Self-host &amp; develop</a> &nbsp;·&nbsp;
  <a href="https://github.com/Pexafy/pexafy-mcp/blob/main/CHANGELOG.md">Changelog</a> &nbsp;·&nbsp;
  <a href="https://pexafy.com/legal/privacy/">Privacy</a> &nbsp;·&nbsp;
  <a href="https://github.com/Pexafy/pexafy-mcp/blob/main/LICENSE">MIT licence</a>
</p>

<p align="center"><sub>ChatGPT, Claude, Cursor and Visual Studio Code are trademarks of their respective owners.</sub></p>
