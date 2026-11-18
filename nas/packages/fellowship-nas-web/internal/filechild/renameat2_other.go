//go:build linux && !amd64 && !arm64

package filechild

// Elsewhere the rename looks first, then renames
const sysRenameat2 = -1
