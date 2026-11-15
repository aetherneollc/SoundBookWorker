import unittest

from renderer.rig_characters import _classify_view_angle, resolve_slot_view_angle


class ViewAngleSlotTests(unittest.TestCase):
    def test_narrow_body_is_labeled_side(self):
        self.assertEqual(_classify_view_angle(100, 400, 0.0), 'side')
        self.assertLess(100 / 400, 0.34)

    def test_master_slot_is_three_quarter_even_when_the_body_is_narrow(self):
        classified = _classify_view_angle(90, 420, 0.02)
        self.assertEqual(classified, 'side')
        self.assertEqual(resolve_slot_view_angle('master', classified), 'three_quarter')

    def test_side_slot_stays_side(self):
        self.assertEqual(resolve_slot_view_angle('side', 'front'), 'side')
        self.assertEqual(resolve_slot_view_angle('side', 'three_quarter'), 'side')

    def test_unknown_slot_keeps_the_heuristic(self):
        self.assertEqual(resolve_slot_view_angle(None, 'front'), 'front')
        self.assertEqual(resolve_slot_view_angle(None, 'three_quarter'), 'three_quarter')


if __name__ == '__main__':
    unittest.main()
