package masterdata

import "testing"

func TestDefaultFilesIncludeWorldBloomFrameMasterdata(t *testing.T) {
	wanted := []string{"playerFrames.json", "playerFrameGroups.json", "playerFrameParts.json"}
	for _, name := range wanted {
		found := false
		for _, file := range defaultFiles {
			if file == name {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("defaultFiles does not include %s", name)
		}
	}
}
