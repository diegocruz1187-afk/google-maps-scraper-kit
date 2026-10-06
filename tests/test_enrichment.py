import unittest

from render.enrichment import dedupe_and_rank, normalize_row


class EnrichmentRankingTests(unittest.TestCase):
    def test_power_dent_scores_as_strong_match(self):
        row = {
            "title": "PowerDent - Escova de Dente - Fio Dental",
            "website": "https://www.pdhb.com.br/",
            "phone": "+55 11 4161-8720",
            "emails": "mkt5@pdhb.com.br",
            "address": "Santana de Parnaíba - SP, Brasil",
            "complete_address": '{"city":"Santana de Parnaíba","state":"São Paulo","country":"BR"}',
            "review_count": "78",
            "review_rating": "4.6",
        }
        item = normalize_row(
            row,
            {"company_name": "Power Dent", "city": "São Paulo", "state": "SP"},
        )
        self.assertGreaterEqual(item["match_score"], 65)
        self.assertEqual(item["domain"], "pdhb.com.br")
        self.assertEqual(item["emails"], ["mkt5@pdhb.com.br"])

    def test_mixpac_beats_unrelated_mix_candidate(self):
        rows = [
            {
                "title": "Mix Descartáveis | Luvas, Toucas, Propé",
                "website": "http://www.mixdescartaveis.com/",
                "phone": "+55 11 99481-5810",
                "emails": "nuvempago@2x.png, visa@2x.png",
                "address": "São Paulo - SP",
                "complete_address": '{"city":"São Paulo","state":"São Paulo","country":"BR"}',
                "review_count": "107",
            },
            {
                "title": "MIXPAC Brasil",
                "website": "https://www.medmix.swiss/pt-BR",
                "phone": "+55 11 94542-1963",
                "emails": "contato_brazil@medmix.com",
                "address": "Cotia - SP",
                "complete_address": '{"city":"Cotia","state":"São Paulo","country":"BR"}',
                "review_count": "0",
            },
        ]
        ranked = dedupe_and_rank(
            rows,
            {"company_name": "MIXPAC", "city": "São Paulo", "state": "SP"},
        )
        self.assertEqual(ranked[0]["name"], "MIXPAC Brasil")
        self.assertEqual(ranked[0]["domain"], "medmix.swiss")
        self.assertEqual(ranked[0]["emails"], ["contato_brazil@medmix.com"])
        self.assertEqual(ranked[1]["emails"], [])

    def test_lmg_complete_listing_ranks_above_sparse_listing(self):
        rows = [
            {
                "title": "LMG Lasers",
                "phone": "+55 11 2361-8923",
                "address": "São Paulo - SP",
                "complete_address": '{"city":"São Paulo","state":"São Paulo","country":"BR"}',
                "review_count": "0",
            },
            {
                "title": "LMG Lasers",
                "phone": "+55 35 3559-2512",
                "website": "http://www.lmglasers.com.br/",
                "emails": "contato@lmglasers.com.br",
                "address": "São Paulo - SP",
                "complete_address": '{"city":"São Paulo","state":"São Paulo","country":"BR"}',
                "review_count": "10",
            },
        ]
        ranked = dedupe_and_rank(
            rows,
            {"company_name": "LMG Lasers", "city": "São Paulo", "state": "SP"},
        )
        self.assertEqual(ranked[0]["domain"], "lmglasers.com.br")
        self.assertGreater(ranked[0]["match_score"], ranked[1]["match_score"])
        self.assertIsNotNone(ranked[0]["possible_same_entity_group"])
        self.assertEqual(
            ranked[0]["possible_same_entity_group"],
            ranked[1]["possible_same_entity_group"],
        )


if __name__ == "__main__":
    unittest.main()
