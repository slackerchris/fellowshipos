package helper

import (
	"testing"
	"time"
)

func TestCPUPercentCountsIowaitAsIdleAndKeepsStealApart(t *testing.T) {
	before := CPUTicks{Total: 1000, Idle: 800, Steal: 10}
	// 200 more ticks: 80 idle+iowait, 20 steal, 120 work
	after := CPUTicks{Total: 1200, Idle: 880, Steal: 30}
	busy, steal := CPUPercent(before, after)
	if busy != 60 || steal != 10 {
		t.Fatalf("busy %v steal %v, want 60 and 10", busy, steal)
	}
	if busy, steal := CPUPercent(before, before); busy != 0 || steal != 0 {
		t.Fatalf("no elapsed ticks: %v %v", busy, steal)
	}
}

func TestTheSamplerSmoothsOverTenSecondsAndKeepsFiveMinutes(t *testing.T) {
	s := &CPUSampler{}
	now := time.Unix(1000, 0)
	s.add(now.Add(-6*time.Minute), 10, 0) // older than the history window
	s.add(now.Add(-9*time.Second), 20, 0)
	s.add(now.Add(-3*time.Second), 40, 0)
	s.add(now.Add(-30*time.Second), 90, 0) // outside the smoothing window
	busy, _ := s.Now(now)
	if busy != 30 {
		t.Fatalf("smoothed %v, want the mean of the last ten seconds (30)", busy)
	}
	for _, sample := range s.History() {
		if sample.Time < now.Add(-5*time.Minute).Unix() {
			t.Fatalf("history kept a sample older than five minutes: %+v", sample)
		}
	}
	if busy, _ := s.Now(now.Add(20 * time.Second)); busy != 0 {
		t.Fatalf("a quiet window should read zero, got %v", busy)
	}
}
