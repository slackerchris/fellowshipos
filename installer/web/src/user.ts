// The user name to suggest for a full name, the way most installers do it: the
// first word only, in lower case, with accents and anything a user name can't
// hold dropped. "Chris Smith" suggests "chris"; "José Núñez" suggests "jose".
export function suggestUser(fullName: string) {
    const first = fullName.trim().split(/\s+/)[0] ?? "";
    const plain = first.toLowerCase().normalize("NFD").replace(/[^a-z0-9_-]/g, "");
    // A user name starts with a letter or an underscore
    return plain.replace(/^[^a-z_]+/, "").slice(0, 32);
}
