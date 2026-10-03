# Pexafy for Claude

Find free stock photographs without leaving the conversation. Describe the scene you need, in any language, or give a link to an image you want free alternatives to, and Claude searches Pexafy's own index of free-licence photographs, collected from Unsplash, Pexels, Pixabay and other libraries. Each photograph comes with the credit line to display, a link to the image and its page at the source.

Where Claude shows interactive apps in the conversation, the results appear as a grid: narrow it to landscape, portrait or square, like the photographs you want with the heart, and press ≈ on one to see similar photographs. Claude can then read the photographs you liked and work with exactly those.

## What the plugin contains

- **The Pexafy connector**: the remote MCP server at https://mcp.pexafy.com/mcp, declared in `.mcp.json`. It is the same server as the Pexafy connector in Claude's directory, so if you have both you see one set of tools. All of its tools are read-only: they search Pexafy's photo index, return a chosen photograph as an image file, read the photographs you liked in the grid and, where account linking is offered, report whether a Pexafy account is connected.
- **One skill**, `find-stock-photos`, which tells Claude which Pexafy tool fits a request and how to present what comes back.

There are no hooks, scripts or local servers: the plugin installs and runs nothing on your computer.

## Set it up

1. Connect Pexafy. On claude.ai, in the desktop app or in Cowork, open the plugin's **Connectors** tab and connect it. In Claude Code, run `/mcp`: if the Pexafy server is marked as needing authentication, select it and follow the sign-in steps in your browser. Otherwise Claude Code is already using Pexafy without an account, on a small daily allowance.
2. Sign in to Pexafy and approve the single read permission. The connection uses one of your account's API keys; a free account includes one key and 5,000 searches a month, with no card required.

## Examples

- "I'm writing a blog post about remote work and burnout. Find photos of someone working alone late at night in a quiet apartment, lit by a warm desk lamp."
- "Find photos of a train in Japan, landscape only."
- "More like the second one."
- "Find free photos that look like this one, but at night: https://images.pexels.com/photos/4846279/pexels-photo-4846279.jpeg?w=1280"
- "Use the photos I liked in the grid for a short article about Kyoto, with their credit lines."
- "Give me the first photo I liked as a file for my slide."

## Data

The plugin runs no code of its own and stores nothing. Everything goes through the Pexafy connector, over HTTPS, to servers that Pexafy operates; requests pass through Cloudflare, Pexafy's network provider.

- **What Claude sends when it calls a tool**: the search sentence Claude writes, and the shape filter if there is one. For a search by image, the link to the image, which Pexafy then downloads, or the image's bytes, or the identifier of a Pexafy photo. To get a photograph as a file, its identifier. Nothing else from the conversation is sent.
- **What the grid sends**: the photographs you like with the heart, by identifier and in the order you liked them, with their public details, so that Claude can read them back.
- **What Pexafy keeps**: the search text, with the key it was made with and the time, in Pexafy's API call log for 90 days. Reference images are used to run the search and are not stored. Your selection is held in the connector's memory for about an hour and is never written to disk.
- **Who you are**: when you sign in, you approve read access, and the connector gets an API key of its own, used only by its read-only tools, which you can revoke from your Pexafy dashboard. Where the connector offers a small daily allowance without signing in, to clients that call it directly such as Claude Code, it counts that allowance with a salted hash of your IP address, kept with those searches in the call log. Your name and e-mail address are never used for it.
- **What comes back**: photographs, credit lines and links. In the grid, thumbnails load from Pexafy's own image service, and links to pexafy.com carry campaign tags (utm_source, utm_medium, utm_campaign) that tell Pexafy the visit came from the connector. Image links in the results point to the servers of the library each photograph comes from, such as Unsplash, Pexels or Pixabay.

Privacy policy: https://pexafy.com/legal/privacy/

## Troubleshooting

- **Claude does not use Pexafy.** Check that the connector is connected, in the plugin's Connectors tab or with `/mcp` in Claude Code, or ask for it by name: "search Pexafy for photos of …".
- **The sign-in keeps coming back.** Disconnect the connector, connect it again and sign in. Revoking its key from your Pexafy dashboard also ends the connection, for every assistant connected to your account by signing in: they all share that key.
- **All of your plan's API keys are in use.** Connecting by signing in needs one key, shared by every assistant connected that way; a key you created for an editor or the API takes another. Revoke one you no longer use at https://pexafy.com/dashboard/api-keys/, and the connection works again.
- **The allowance is used up.** Claude says so, and says when it resets.
- **An image link is refused.** It must be the public http(s) address of a JPEG, PNG, WebP or AVIF image of 10 MB at most. On claude.ai, a picture attached to the conversation cannot be passed to the connector: share a link to it, or Claude searches for the scene it shows in words instead.
- **No grid appears.** The grid is shown where Claude displays interactive apps. Elsewhere, including Claude Code, the results arrive as links with their credit lines.
- **Is the service up?** https://mcp.pexafy.com/health answers without signing in.

## Support

support@pexafy.com · https://pexafy.com/contact/ · https://pexafy.com/mcp/

## License

MIT, in the LICENSE file of this folder.
