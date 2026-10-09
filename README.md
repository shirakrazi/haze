# Haze page on GitHub (free)

This folder is a complete website. GitHub hosts it for free and runs the update script every 15 minutes.

Your address will be `https://YOUR-USERNAME.github.io/haze/`. The username is the part you choose, so pick it with care when you sign up.

## Setup (about 10 minutes, all in the browser)

1. **Create a GitHub account** at github.com if you do not have one. The username becomes part of the link.
2. **Create a repository:** click **+** (top right), then **New repository**. Name it `haze`, set it to **Public**, and click **Create repository**.
3. **Upload the files:** on the new repository page click **uploading an existing file**. Unzip this package on your computer, open the `haze-github` folder, select everything inside it and drag it in. Click **Commit changes**.
   - The `.github` folder must be included, because it holds the schedule. If your computer hides it (Mac: press Cmd+Shift+. in Finder; Windows: View, then Show hidden items), unhide it first.
4. **Turn the website on:** go to **Settings**, then **Pages**. Under "Build and deployment", set Source to **Deploy from a branch**, Branch to **main** and folder **/ (root)**, then **Save**. After a minute or two the page shows your live address.
5. **Start the updates:** open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**. Click **Update haze data**, then **Run workflow**. A green tick means it worked.
6. **Check:** open your address on a phone. The "Readings for..." time should be within the last hour or so.

## If something goes wrong

- **Red cross in Actions:** click the failed run, then the **update** job, and read the "Pull readings and headlines" step. A news feed that says `failed` is fine. A line saying `PSI fetch failed` means NEA's feed could not be reached from GitHub.
- **Page shows 404:** the Pages setting in step 4 is not saved, or `index.html` is inside a subfolder. It must sit at the top level of the repository.
- **Readings stop moving:** open Actions and check the latest runs. GitHub sometimes delays scheduled runs when it is busy.

## Day to day

- **Pin an article:** edit `pinned.json` on GitHub (pencil icon) and add the source, title, url and summary. Leave `time` empty.
- **Change the page:** replace `index.html` with a new version.
- **Regional map:** the Malaysia, Indonesia and hotspot points are a fixed snapshot from 9 October 2026. Only the Singapore readings and the news update on their own.
