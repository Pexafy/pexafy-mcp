# Pexafy for Cursor

Find real, free-to-use photographs without leaving Cursor. Say what you need, in your own words, for the landing page, README, mock-up or blog post you are working on, or give a link to an image you want free alternatives to: the agent writes the search, and Pexafy matches the scene in its own index of free-licence photographs, collected from Unsplash, Pexels, Pixabay and other libraries. Each photograph comes with the credit line to display, a link to the image and its page at the source.

Then you choose together. In Cursor's chat the results appear as an interactive grid: narrow it to landscape, portrait or square, like the photographs you want with the heart, and press ≈ on one to see similar photographs. The agent reads the photographs you liked, in your order, and works with exactly those. Or let it place the photographs straight into your files, with their credit lines.

## What the plugin contains

- **The Pexafy MCP server**: the remote server at https://mcp.pexafy.com/mcp, declared in `mcp.json`. All of its tools are read-only: they search Pexafy's photo index, return a chosen photograph as an image file, read the photographs you liked in the grid and, when you ask for it, connect a Pexafy account.
- **One skill**, `find-stock-photos`, which tells the agent which Pexafy tool fits a request and how to present what comes back.

There are no rules, hooks, scripts or local servers: the plugin installs and runs nothing on your computer.

## Set it up

1. Install the plugin from the Cursor Marketplace, then check in **Customize** that the `pexafy` MCP server is enabled. MCP servers and skills need a Cursor plan that includes them.
2. Ask for photographs. Pexafy works without an account, on a small daily allowance.
3. To search on your Pexafy account's allowance, ask the agent to connect your Pexafy account, or press the account button in the grid. Cursor then asks you to authenticate the Pexafy server: sign in to Pexafy in your browser and approve the single read permission. A free Pexafy account includes 5,000 searches a month.

## Examples

- "Find a hero photo for this landing page: a small team working around a laptop in a bright office, landscape."
- "Find photos of a train in Japan, landscape only."
- "More like the second one."
- "Find free photos that look like this one, but at night: https://images.pexels.com/photos/4846279/pexels-photo-4846279.jpeg?w=1280"
- "Add the photos I liked in the grid to the page, with their credit lines."

## Data

The plugin runs no code of its own and stores nothing. Everything goes through the Pexafy MCP server, over HTTPS, to servers that Pexafy operates; requests pass through Cloudflare, Pexafy's network provider.

- **What Cursor sends when the agent calls a tool**: the search sentence the agent writes, and the shape filter if there is one. For a search by image, the link to the image, which Pexafy then downloads, or the image's bytes, or the identifier of a Pexafy photo. To get a photograph as a file, its identifier. Nothing else from the conversation is sent.
- **What the grid sends**: the photographs you like with the heart, by identifier and in your order, with their public details, so that the agent can read them back.
- **What Pexafy keeps**: the search text, with the key or the anonymous identifier it was made with and the time, in Pexafy's API call log for 90 days. Reference images are used to run the search and are not stored. Your selection is held in the server's memory for about an hour and is never written to disk.
- **Who you are**: without an account, the daily allowance is counted under a keyed hash (HMAC) of your IP address, kept with those searches in the call log; your name and e-mail address are never used for it. When you sign in, you approve read access, and the server gets an API key of its own, used only by its read-only tools, which you can revoke from your Pexafy dashboard.
- **What comes back**: photographs, credit lines and links. In the grid, thumbnails load from Pexafy's own image service, and links to pexafy.com carry campaign tags (utm_source, utm_medium, utm_campaign) that tell Pexafy the visit came from the connector. Image links in the results point to the servers of the library each photograph comes from, such as Unsplash, Pexels or Pixabay.

Privacy policy: https://pexafy.com/legal/privacy/

## Troubleshooting

- **The agent does not use Pexafy.** Check in **Customize** that the `pexafy` server is enabled, or ask for it by name: "search Pexafy for photos of …".
- **The grid shows an error instead of the photographs.** Select **Retry**: the first grid after the server restarts can fail to load once.
- **The sign-in keeps coming back.** Disconnect the Pexafy server in Cursor, connect it again and sign in. Revoking its key from your Pexafy dashboard also ends the connection, for every assistant connected to your account by signing in: they all share that key.
- **All of your plan's API keys are in use.** Connecting by signing in needs one key, shared by every assistant connected that way; a key you created for an editor or the API takes another. Revoke one you no longer use at https://pexafy.com/dashboard/api-keys/, and the connection works again.
- **The allowance is used up.** The agent says so, and says when it resets.
- **An image link is refused.** It must be the public http(s) address of a JPEG, PNG, WebP or AVIF image of 10 MB at most.
- **Is the service up?** https://mcp.pexafy.com/health answers without signing in.

## Support

support@pexafy.com · https://pexafy.com/contact/ · https://pexafy.com/mcp/

## License

MIT, in the LICENSE file of this folder.
