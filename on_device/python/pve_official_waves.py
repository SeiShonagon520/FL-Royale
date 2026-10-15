"""Official PvE wave scripts, generated from the decrypted pve_waves.csv.

GENERATED FILE -- do not edit by hand. Regenerate with:

    python tools\\build_official_pve_waves.py

Source: assets/csv_logic/pve_waves.csv, content version 15.535.86.
Exported 12 of 50 named waves; 38 are skipped
because their action set is outside the deploy-expressible subset (Clan
Boat tower logic, card-delivery campaign chains) or because a spawn
point lands inside the 1v1 opponent tower footprint.

`time_in_line` is in milliseconds, exactly as the official table writes it.
The engine runs at 20 ticks/second, so a caller must convert:
`tick = time_in_line_ms * 20 // 1000`.
"""

TICKS_PER_SECOND = 20

GRID_WIDTH = 18
GRID_HEIGHT = 32
CELL_UNITS = 1000
WORLD_HEIGHT = 32000

WAVES_BY_NAME = {
    'CardDelivery_LittlePrince_1': {
        'name': 'CardDelivery_LittlePrince_1',
        'end_condition': 'false',
        'restart_after_ms': None,
        'action_types': ['Spawn', 'SpawnWaveStandalone'],
        'standalone': ['CardDelivery_LittlePrince_1_loop'],
        'spawns': [
            {'card': 26000000, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 19000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000001, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 3000, 'official_y': 9000},
        ],
    },
    'CardDelivery_LittlePrince_3': {
        'name': 'CardDelivery_LittlePrince_3',
        'end_condition': 'false',
        'restart_after_ms': None,
        'action_types': ['Spawn', 'SpawnWaveStandalone'],
        'standalone': ['CardDelivery_LittlePrince_3_loop'],
        'spawns': [
            {'card': 26000093, 'x': 3500, 'y': 30500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 6000', 'official_x': 3000, 'official_y': 1000},
        ],
    },
    'CardDelivery_LittlePrince_1_loop': {
        'name': 'CardDelivery_LittlePrince_1_loop',
        'end_condition': 'false',
        'restart_after_ms': 30000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000000, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000001, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 9000},
            {'card': 26000000, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 30000', 'official_x': 14000, 'official_y': 11000},
            {'card': 26000001, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 14000, 'official_y': 9000},
        ],
    },
    'CardDelivery_DaggerDuchess_1_loop': {
        'name': 'CardDelivery_DaggerDuchess_1_loop',
        'end_condition': 'false',
        'restart_after_ms': 25000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000027, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000005, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1500', 'official_x': 3000, 'official_y': 9000},
            {'card': 26000027, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 25000', 'official_x': 14000, 'official_y': 11000},
            {'card': 26000005, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1500', 'official_x': 14000, 'official_y': 9000},
        ],
    },
    'CardDelivery_DaggerDuchess_2_loop': {
        'name': 'CardDelivery_DaggerDuchess_2_loop',
        'end_condition': 'false',
        'restart_after_ms': 28000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000003, 'x': 3500, 'y': 23500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 8000},
            {'card': 26000019, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 4000', 'official_x': 3000, 'official_y': 9000},
            {'card': 26000013, 'x': 8500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 10000', 'official_x': 8500, 'official_y': 9000},
            {'card': 26000003, 'x': 14500, 'y': 23500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 28000', 'official_x': 14000, 'official_y': 8000},
            {'card': 26000019, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 4000', 'official_x': 14000, 'official_y': 9000},
        ],
    },
    'CardDelivery_DaggerDuchess_3_loop': {
        'name': 'CardDelivery_DaggerDuchess_3_loop',
        'end_condition': 'false',
        'restart_after_ms': 30000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000055, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000058, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 14000, 'official_y': 11000},
            {'card': 27000004, 'x': 8500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 10000', 'official_x': 8500, 'official_y': 9000},
            {'card': 26000058, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 25000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000055, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 14000, 'official_y': 11000},
        ],
    },
    'GTR_Tutorial_loop': {
        'name': 'GTR_Tutorial_loop',
        'end_condition': 'false',
        'restart_after_ms': 30000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000003, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000001, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 3000, 'official_y': 9000},
            {'card': 26000003, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 25000', 'official_x': 14000, 'official_y': 11000},
            {'card': 26000001, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 14000, 'official_y': 9000},
        ],
    },
    'CardDelivery_Goblinstein_1_loop': {
        'name': 'CardDelivery_Goblinstein_1_loop',
        'end_condition': 'false',
        'restart_after_ms': 25000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000016, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000001, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 3000, 'official_y': 9000},
            {'card': 26000016, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 25000', 'official_x': 14000, 'official_y': 11000},
            {'card': 26000001, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 14000, 'official_y': 9000},
        ],
    },
    'CardDelivery_Goblinstein_2_loop': {
        'name': 'CardDelivery_Goblinstein_2_loop',
        'end_condition': 'false',
        'restart_after_ms': 28000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000003, 'x': 3500, 'y': 23500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 8000},
            {'card': 26000014, 'x': 8500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 5000', 'official_x': 8500, 'official_y': 9000},
            {'card': 26000003, 'x': 14500, 'y': 23500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 20000', 'official_x': 14000, 'official_y': 8000},
            {'card': 26000014, 'x': 8500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 5000', 'official_x': 8500, 'official_y': 9000},
        ],
    },
    'CardDelivery_RuneBerserker_1_loop': {
        'name': 'CardDelivery_RuneBerserker_1_loop',
        'end_condition': 'false',
        'restart_after_ms': 30000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000024, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000001, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 3000, 'official_y': 9000},
            {'card': 26000024, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 30000', 'official_x': 14000, 'official_y': 11000},
            {'card': 26000001, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 14000, 'official_y': 9000},
        ],
    },
    'CardDelivery_RuneBerserker_2_Log': {
        'name': 'CardDelivery_RuneBerserker_2_Log',
        'end_condition': 'false',
        'restart_after_ms': None,
        'action_types': ['Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 28000011, 'x': 7500, 'y': 17500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 100', 'official_x': 7500, 'official_y': 14000},
        ],
    },
    'CardDelivery_RuneBerserker_2_loop': {
        'name': 'CardDelivery_RuneBerserker_2_loop',
        'end_condition': 'false',
        'restart_after_ms': 30000,
        'action_types': ['RestartWave', 'Spawn'],
        'standalone': [],
        'spawns': [
            {'card': 26000003, 'x': 14500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 1000', 'official_x': 14000, 'official_y': 11000},
            {'card': 26000041, 'x': 14500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 14000, 'official_y': 9000},
            {'card': 26000003, 'x': 3500, 'y': 20500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 30000', 'official_x': 3000, 'official_y': 11000},
            {'card': 26000041, 'x': 3500, 'y': 22500, 'count': 1, 'time_in_line_ms': 'time_in_line >= 2000', 'official_x': 3000, 'official_y': 9000},
        ],
    },
}

EXCLUDED_WAVES = ['CardDelivery_DaggerDuchess_1', 'CardDelivery_DaggerDuchess_2', 'CardDelivery_DaggerDuchess_3', 'CardDelivery_Goblinstein_1', 'CardDelivery_Goblinstein_2', 'CardDelivery_LittlePrince_2', 'CardDelivery_LittlePrince_2_loop', 'CardDelivery_LittlePrince_3_loop', 'MainClanBoatAttack', 'MainClanBoatAttackBrokenTower', 'MainClanBoatAttackRevengeTower_SPAWNONDMG', 'MainClanBoatAttackRevengeTower_SPAWNONHIT', 'MainClanBoatAttackTower', 'PVE_Test_NPC_1', 'Tower1SpawnAttacker', 'Tower1SpawnDefender', 'Tower1SpawnRange', 'Tower1SpawnSpell', 'Tower1SpawnUnknown', 'Tower2SpawnAttacker', 'Tower2SpawnDefender', 'Tower2SpawnRange', 'Tower2SpawnSpell', 'Tower2SpawnUnknown', 'Tower3SpawnAttacker', 'Tower3SpawnDefender', 'Tower3SpawnRange', 'Tower3SpawnSpell', 'Tower3SpawnUnknown', 'Tutorial1', 'Tutorial1Repeat', 'Tutorial2', 'TutorialTest_1_1', 'TutorialTest_1_2', 'TutorialTest_1_3', 'Tutorial_Empty_Wave', 'Tutorial_NPC_4_2', 'Tutorial_NPC_5_2']

# Official spawns that are illegal on the 1v1 arena because they land
# inside an opponent princess-tower footprint. Each entry is
# (wave, card name, official x, official y, arena x, arena y, cell x, cell y).
# These are NOT nudged: moving them would invent a wave the official
# script never specified. They are the one hard blocker on a real
# single-sided PvE map, and they are pinned by a test.
TOWER_FOOTPRINT_CLASHES = [
    ('PVE_Test_NPC_1', 'Archer', 15000, 7500, 15500, 24500, 15, 24),
    ('PVE_Test_NPC_1', 'Bomber', 15000, 7500, 15500, 24500, 15, 24),
    ('PVE_Test_NPC_1', 'Giant', 3500, 7500, 3500, 24500, 3, 24),
    ('PVE_Test_NPC_1', 'Knight', 15000, 7500, 15500, 24500, 15, 24),
    ('Tutorial1', 'Skeletons', 3500, 7500, 3500, 24500, 3, 24),
    ('Tutorial1Repeat', 'Skeletons', 3500, 7500, 3500, 24500, 3, 24),
    ('Tutorial2', 'Goblins', 4000, 7000, 4500, 24500, 4, 24),
    ('TutorialTest_1_1', 'Skeletons', 3500, 7500, 3500, 24500, 3, 24),
    ('TutorialTest_1_2', 'Goblins', 4000, 7000, 4500, 24500, 4, 24),
    ('TutorialTest_1_3', 'Goblins', 15000, 7000, 15500, 24500, 15, 24),
    ('Tutorial_NPC_4_2', 'Archer', 3500, 7500, 3500, 24500, 3, 24),
    ('Tutorial_NPC_4_2', 'Goblins', 15000, 7500, 15500, 24500, 15, 24),
    ('Tutorial_NPC_4_2', 'Knight', 3500, 7500, 3500, 24500, 3, 24),
    ('Tutorial_NPC_4_2', 'Skeletons', 15000, 7500, 15500, 24500, 15, 24),
    ('Tutorial_NPC_5_2', 'Archer', 15000, 7500, 15500, 24500, 15, 24),
    ('Tutorial_NPC_5_2', 'Bomber', 15000, 7500, 15500, 24500, 15, 24),
    ('Tutorial_NPC_5_2', 'Knight', 15000, 7500, 15500, 24500, 15, 24),
    ('Tutorial_NPC_5_2', 'Skeletons', 3500, 7500, 3500, 24500, 3, 24),
]
