// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"bytes"
	"context"
	"flag"
	ipp "github.com/OpenPrinting/goipp"
	"net"
	"net/http"
	"os/user"
	"strings"
	"time"
)

var localQueue = flag.String("queue", "", "source macOS CUPS queue")
var vendorKeys = []string{"Duplex", "HPDuplexMode", "cupsPrintQuality", "Resolution", "MediaType", "InputSlot", "PageSize", "HPOut", "HPReload", "HPHead", "HPEcon", "HPEdge", "HPText", "HPBlack", "HPLayout", "HPNup", "HPBorder", "HPSubset", "HPReverse", "HPSkip", "HPBrightness", "HPContrast"}

func captureTicket(name, owner string) map[string]string {
	if *localQueue == "" || name == "" {
		return nil
	}
	me, e := user.Current()
	if e != nil || (owner != "" && owner != me.Username) {
		return nil
	}
	u := "ipp://localhost/printers/" + *localQueue
	req := ipp.NewRequest(ipp.DefaultVersion, ipp.OpGetJobs, 1)
	keys := append([]string{"job-state", "job-name", "job-originating-user-name"}, vendorKeys...)
	req.Operation = ipp.Attributes{str("attributes-charset", ipp.TagCharset, "utf-8"), str("attributes-natural-language", ipp.TagLanguage, "en"), str("printer-uri", ipp.TagURI, u), str("requesting-user-name", ipp.TagName, me.Username), str("which-jobs", ipp.TagKeyword, "not-completed"), str("requested-attributes", ipp.TagKeyword, keys...)}
	data, e := req.EncodeBytes()
	if e != nil {
		return nil
	}
	tr := &http.Transport{DisableKeepAlives: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", "/private/var/run/cupsd")
	}}
	client := http.Client{Transport: tr, Timeout: 2 * time.Second}
	resp, e := client.Post("http://localhost/printers/"+*localQueue, "application/ipp", bytes.NewReader(data))
	if e != nil {
		return nil
	}
	defer resp.Body.Close()
	var reply ipp.Message
	if reply.Decode(resp.Body) != nil {
		return nil
	}
	for _, g := range reply.Groups {
		if g.Tag != ipp.TagJobGroup || first(g.Attrs, "job-state") != "5" || first(g.Attrs, "job-name") != name || first(g.Attrs, "job-originating-user-name") != me.Username {
			continue
		}
		matched := map[string]string{}
		for _, key := range vendorKeys {
			if val := first(g.Attrs, key); val != "" && !strings.ContainsRune(val, 0) {
				matched[key] = val
			}
		}
		return matched
	}
	return nil
}
