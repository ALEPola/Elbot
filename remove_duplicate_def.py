from pathlib import Path

path = Path(r"src\elbot\config.py")
lines = path.read_text().splitlines()
first_idx = next(i for i, line in enumerate(lines) if line.startswith('def _select_dynamic_lavalink_port'))
# Remove any subsequent occurrences of this def block
indices_to_keep = list(range(len(lines)))
i = first_idx + 1
while i < len(indices_to_keep):
    idx = indices_to_keep[i]
    if lines[idx].startswith('def _select_dynamic_lavalink_port'):
        # remove block starting at idx until blank line after function
        j = idx
        while j < len(lines) and (lines[j].strip() != '' or j == idx):
            indices_to_keep.remove(j)
            lines[j] = None
            j += 1
        if j < len(lines) and lines[j] == '':
            indices_to_keep.remove(j)
            lines[j] = None
        break
    i += 1
new_lines = [line for line in lines if line is not None]
path.write_text("\n".join(new_lines) + "\n")
