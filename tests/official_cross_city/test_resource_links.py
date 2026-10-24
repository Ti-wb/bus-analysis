"""Synthetic regressions matching data.gov.tw's observed resource-item markup."""
import re
import unittest
from pathlib import Path

from scripts.official_cross_city import common, population, metro_od
from test_collectors import FixtureCase


class ResourceLinkTests(unittest.TestCase):
    def row(self, month="11509", href="https://opdadm.moi.gov.tw/right", fmt="CSV"):
        return (f'<li class="resource-item"><a href="{href}"><button><span>{fmt}</span></button></a>'
                f'<button><span>檢視資料</span></button><span>{month}-村里戶數、單一年齡人口</span></li>')

    def select(self, html):
        return common.monthly_link(html, population.PAGE, "2026-09", population.HOSTS)

    def test_adjacent_month_label_in_official_resource_row(self):
        self.assertEqual(self.select(self.row()), "https://opdadm.moi.gov.tw/right")

    def test_neighboring_rows_do_not_supply_month(self):
        html = '<ul>' + self.row("11508", "https://opdadm.moi.gov.tw/old") + self.row() + self.row("11510", "https://opdadm.moi.gov.tw/new") + '</ul>'
        self.assertEqual(self.select(html), "https://opdadm.moi.gov.tw/right")
        with self.assertRaises(ValueError):
            self.select('<div><span>11509</span>' + self.row("11508") + '</div>')

    def test_nested_label_and_entities(self):
        self.assertEqual(self.select(self.row("<span>11509</span>&nbsp;")), "https://opdadm.moi.gov.tw/right")

    def test_multiple_months_in_one_row_are_ambiguous(self):
        with self.assertRaises(ValueError):
            self.select(self.row("11508 11509"))

    def test_duplicate_same_url_but_distinct_urls_are_ambiguous(self):
        self.assertEqual(self.select(self.row() * 2), "https://opdadm.moi.gov.tw/right")
        with self.assertRaises(ValueError):
            self.select(self.row() + self.row(href="https://opdadm.moi.gov.tw/other"))

    def test_only_explicit_csv_format_is_selected(self):
        extra = '<a href="https://opdadm.moi.gov.tw/json"><span>JSON</span></a>'
        self.assertEqual(self.select(self.row().replace('</li>', extra + '</li>')), "https://opdadm.moi.gov.tw/right")
        for fmt in ["JSON", "XML", "Download", "CSV JSON"]:
            with self.subTest(fmt=fmt), self.assertRaises(ValueError):
                self.select(self.row(fmt=fmt))

    def test_transport_restrictions_unchanged(self):
        for href in ["http://opdadm.moi.gov.tw/x", "https://u:p@opdadm.moi.gov.tw/x",
                     "https://opdadm.moi.gov.tw:8443/x", "https://opdadm.moi.gov.tw/x#fragment",
                     "https://opdadm.moi.gov.tw.evil.example/x"]:
            with self.subTest(href=href), self.assertRaises(ValueError):
                self.select(self.row(href=href))

    def test_headings_scripts_comments_and_modal_do_not_supply_month(self):
        for label in ['<h1>11509</h1>', '<script>11509</script>', '<!--11509-->',
                      '<div role="dialog"><span>11509</span></div>']:
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.select(self.row("").replace('</li>', label + '</li>'))

    def test_dated_anchor_cannot_override_resource_label(self):
        with self.assertRaises(ValueError):
            self.select(self.row("11508", fmt="11509 CSV"))

    def test_modal_link_is_not_a_resource_download(self):
        html = self.row(fmt="JSON").replace('</li>', '<div role="dialog"><a href="https://opdadm.moi.gov.tw/modal">CSV</a></div></li>')
        with self.assertRaises(ValueError):
            self.select(html)

    def test_exact_month_token(self):
        for month in ["115090", "211509", "11508", "11510"]:
            with self.subTest(month=month), self.assertRaises(ValueError):
                self.select(self.row(month))

    def test_unclosed_or_nested_resource_rows_fail_closed(self):
        for html in [self.row().replace('</li>', ''), self.row().replace('</li>', self.row() + '</li>')]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                self.select(html)


class DocumentedMetroExampleTests(FixtureCase):
    def test_documented_station_names_match_source_exactly(self):
        doc = (Path(__file__).resolve().parents[2] / 'docs/official-cross-city.md').read_text()
        origins = set(re.search(r"--origins '([^']+)'", doc).group(1).split(','))
        destinations = set(re.search(r"--destinations '([^']+)'", doc).group(1).split(','))
        # Observed 2026-09 official CSV: ingress 頭前庄, egress O頭前庄.
        # Keep those dictionaries separate rather than inventing an alias.
        ingress = ['新莊', '頭前庄', '關渡', '士林']
        egress = ['新莊', 'O頭前庄', '關渡', '士林']
        rows = [{'日期': '2026-09-01', '時段': '7', '進站': o, '出站': d, '人次': '1'}
                for o in ingress for d in egress]
        path = self.csv('m.csv', rows)
        result = metro_od.normalize(path, '2026-09', self.source, origins, destinations, bidirectional=True)
        self.assertEqual(len(result['records']), 4)
        for ambiguous_name in ['頭前庄', 'O頭前庄']:
            with self.subTest(station=ambiguous_name), self.assertRaises(ValueError):
                metro_od.normalize(path, '2026-09', self.source,
                                   {'新莊', ambiguous_name}, destinations, bidirectional=True)
        directed = metro_od.normalize(path, '2026-09', self.source, {'頭前庄'}, {'O頭前庄'})
        self.assertEqual(directed['records'][0]['origin_station_name'], '頭前庄')
        self.assertEqual(directed['records'][0]['destination_station_name'], 'O頭前庄')
