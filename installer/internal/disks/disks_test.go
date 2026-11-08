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
