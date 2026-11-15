package disks

import (
	"strings"
	"testing"
)

const listing = `{"blockdevices": [
 {"name":"sda","path":"/dev/sda","size":500107862016,"model":"Samsung SSD 870 ","serial":"S1","type":"disk","tran":"sata","ro":false,"fstype":null,"label":null,"mountpoints":[null],"children":[]},
 {"name":"sdb","path":"/dev/sdb","size":8001563222016,"model":"WDC WD80","serial":"W1","type":"disk","tran":"sata","ro":false,"fstype":null,"label":null,"mountpoints":[null],"children":[
   {"name":"sdb1","path":"/dev/sdb1","size":8001563222016,"model":null,"serial":null,"type":"part","tran":null,"ro":false,"fstype":"zfs_member","label":"tank","mountpoints":[null]}]},
 {"name":"sdc","path":"/dev/sdc","size":16008609792,"model":"USB Stick","serial":"U1","type":"disk","tran":"usb","ro":false,"fstype":"iso9660","label":"FellowshipOS","mountpoints":["/run/live/medium"],"children":[]},
 {"name":"sdd","path":"/dev/sdd","size":4000787030016,"model":"Old","serial":"O1","type":"disk","tran":"sata","ro":false,"fstype":null,"label":null,"mountpoints":[null],"children":[
   {"name":"sdd1","path":"/dev/sdd1","size":4000787030016,"model":null,"serial":null,"type":"part","tran":null,"ro":false,"fstype":"ntfs","label":"Backup","mountpoints":["/mnt"]}]},
 {"name":"sr0","path":"/dev/sr0","size":1073741312,"model":"DVD","serial":null,"type":"rom","tran":"sata","ro":true,"fstype":null,"label":null,"mountpoints":[null]},
 {"name":"zram0","path":"/dev/zram0","size":1073741824,"model":null,"serial":null,"type":"disk","tran":null,"ro":false,"fstype":"swap","label":null,"mountpoints":["[SWAP]"]},
 {"name":"fd0","path":"/dev/fd0","size":4096,"model":null,"serial":null,"type":"disk","tran":null,"ro":false,"fstype":null,"label":null,"mountpoints":[null]},
 {"name":"vda","path":"/dev/vda","size":8589934592,"model":null,"serial":"V1","type":"disk","tran":"virtio","ro":false,"fstype":null,"label":null,"mountpoints":[null]}
]}`

func TestParse(t *testing.T) {
	found, err := Parse([]byte(listing), "/dev/sdc", 16<<30)
	if err != nil {
		t.Fatal(err)
	}
	byPath := map[string]Disk{}
	for _, d := range found {
		byPath[d.Path] = d
	}
	if len(found) != 5 {
		t.Fatalf("found %d disks: %+v", len(found), found)
	}
	if d := byPath["/dev/sda"]; d.Refused != "" || len(d.Contents) != 0 || d.Model != "Samsung SSD 870" {
		t.Errorf("blank disk: %+v", d)
	}
	if d := byPath["/dev/sdb"]; d.Refused != "" || d.Contents[0] != `part of ZFS pool "tank"` {
		t.Errorf("pool disk: %+v", d)
	}
	if !strings.Contains(byPath["/dev/sdc"].Refused, "running from") {
		t.Errorf("live disk: %+v", byPath["/dev/sdc"])
	}
	if !strings.Contains(byPath["/dev/sdd"].Refused, "in use") ||
		byPath["/dev/sdd"].Contents[0] != `a Windows (NTFS) file system labelled "Backup"` {
		t.Errorf("mounted disk: %+v", byPath["/dev/sdd"])
	}
	if !strings.Contains(byPath["/dev/vda"].Refused, "small") {
		t.Errorf("small disk: %+v", byPath["/dev/vda"])
	}
}

// Booted with toram, the live system runs from memory: the stick isn't mounted
// and LiveDisk names no disk, but the stick must still not be offered
func TestAnInstallerStickIsRefusedAfterTheSystemMovedToMemory(t *testing.T) {
	stick := `{"blockdevices": [
 {"name":"sda","path":"/dev/sda","size":64023257088,"model":"SanDisk","serial":"S","type":"disk","tran":"usb","ro":false,"fstype":"iso9660","label":"FELLOWSHIPOS_NAS","mountpoints":[null],"children":[
   {"name":"sda1","path":"/dev/sda1","size":1029476352,"model":null,"serial":null,"type":"part","tran":null,"ro":false,"fstype":"iso9660","label":"FELLOWSHIPOS_NAS","mountpoints":[null]}]},
 {"name":"sdb","path":"/dev/sdb","size":64023257088,"model":"Other stick","serial":"O","type":"disk","tran":"usb","ro":false,"fstype":"iso9660","label":"DEBIAN","mountpoints":[null],"children":[]}
]}`
	found, err := Parse([]byte(stick), "tmpfs", 16<<30)
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 2 || !strings.Contains(found[0].Refused, "installer USB stick") {
		t.Fatalf("installer stick: %+v", found)
	}
	// Any other ISO stick is an ordinary disk to install on
	if found[1].Refused != "" {
		t.Fatalf("other stick: %+v", found[1])
	}
}
