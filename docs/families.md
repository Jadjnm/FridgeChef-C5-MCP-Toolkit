# Ingredient families

What a fridge item resolves to in TheMealDB's ingredient catalogue (992 names, snapshot in
`tests/data/catalogue.json`). `search_recipes` searches every name in the family, and `cook_tonight`
counts a recipe ingredient as "in the fridge" when it is in the family. Generated from `matching.py`;
closest names first.

Families describe what the fridge item can supply. Whole lemons and eggs include their parts;
lemon juice/zest and separated egg whites/yolks do not include the whole food or the other part.

| Fridge item | Names | Family |
|---|---|---|
| chicken | 8 | Chicken, Chicken Breast, Chicken Breasts, Chicken drumsticks, Chicken Legs, Chicken Thighs, Chicken Wings, Ground Chicken |
| beef | 10 | Beef, Beef Brisket, Beef Cutlet, Beef Fillet, Beef Shoulder, Beef Tenderloin, Corned Beef, Ground Beef, Minced Beef, Feather blade beef |
| lamb | 6 | Lamb, Lamb Leg, Lamb Mince, Lamb Shanks, Lamb Shoulder, Lamb Loin Chops |
| pork | 8 | Pork, Ground Pork, Minced Pork, Pork Cheeks, Pork Chops, Pork Shoulder, Pork Tenderloin, Pork Shoulder Steaks |
| salmon | 3 | Salmon, Smoked Salmon, Hot Smoked Flaked Salmon |
| prawns | 6 | Prawns, King Prawns, Tiger Prawns, Raw Frozen Prawns, Raw King Prawns, Raw tiger prawns |
| tofu | 3 | Tofu, Marinated Tofu, Silken Tofu |
| eggs | 6 | Egg, Eggs, Egg White, Egg Yolks, Free-range Egg, Beaten, Free-range Eggs, Beaten |
| potato | 10 | Potatoes, Charlotte Potatoes, Floury Potatoes, New Potatoes, Red Potatoes, Russet Potato, Small Potatoes, Baby New Potatoes, Jersey Royal Potatoes, Yukon Gold Potatoes |
| onion | 6 | Onion, Onions, Chopped Onion, Ground Onion, Red Onions, Yellow Onion |
| garlic | 4 | Garlic, Garlic Clove, Minced Garlic, Pickled Garlics |
| tomato | 12 | Tomato, Tomatoes, Canned Tomatoes, Cherry Tomatoes, Chopped Tomatoes, Diced Tomatoes, Grape Tomatoes, Plum Tomatoes, Tinned Tomatos, Vine Tomatoes … (+2 more) |
| carrot | 1 | Carrots |
| mushroom | 5 | Mushrooms, Chestnut Mushroom, Shiitake Mushrooms, Wild Mushrooms, Wood Ear Mushrooms |
| spinach | 1 | Spinach |
| peas | 2 | Peas, Frozen Peas |
| chilli | 7 | Chilli, Ancho Chillies, Dried Chillies, Green Chilli, Red Chilli, Birds-eye Chillies, Dried Red Chillies |
| pepper | 2 | Pepper, Kampot Pepper |
| lemon | 5 | Lemon, Lemons, Lemon Juice, Lemon Zest, Unwaxed Lemon |
| apple | 4 | Apples, Braeburn Apples, Bramley Apples, Cooking Apples |
| rice | 9 | Rice, Arabic Rice, Basmati Rice, Brown Rice, Dessert Rice, Glutinous Rice, Jasmine Rice, Paella Rice, Sushi Rice |
| pasta | 16 | Bowtie Pasta, Linguine Pasta, Paccheri Pasta, Pappardelle Pasta, Penne Pasta, Vermicelli Pasta, Farfalle, Fettuccine, Macaroni, Rigatoni … (+6 more) |
| bread | 8 | Bread, Crusty Bread, Cuban Bread, Naan Bread, Pita Bread, Stale bread, White bread, Wholegrain Bread |
| flour | 8 | Flour, Plain Flour, Tempura Flour, White Flour, All purpose flour, Self-raising Flour, Strong White Flour, Strong Wholemeal Flour |
| milk | 3 | Milk, Whole Milk, Semi-skimmed Milk |
| cream | 6 | Cream, Clotted Cream, Double Cream, Heavy Cream, Single Cream, Whipping Cream |
| butter | 6 | Butter, Chilled Butter, Melted Butter, Salted Butter, Unsalted Butter, Vegan Butter |
| cheese | 19 | Cheese, Bryndza cheese, Cheddar Cheese, Cottage Cheese, Emmentaler Cheese, Farmers Cheese, Goats Cheese, Gouda Cheese, Liquid Cheese, Panquehue Cheese … (+9 more) |
| feta | 1 | Feta |
| yogurt | 4 | Yogurt, Greek Yogurt, Natural Yoghurt, Strained Yoghurt |
| beans | 18 | Baked Beans, Black Beans, Borlotti Beans, Butter Beans, Canned Beans, Cannellini Beans, Haricot Beans, Kidney Beans, Lima Beans, Mung Beans … (+8 more) |
| soy sauce | 2 | Soy Sauce, Dark Soy Sauce |
| ginger | 2 | Ginger, Ground Ginger |
| coriander | 2 | Coriander, Ground Coriander |
| bay leaf | 2 | Bay Leaf, Bay Leaves |
| sugar | 15 | Sugar, Brown Sugar, Caster Sugar, Coco Sugar, Demerara Sugar, Granulated Sugar, Ground Sugar, Muscovado Sugar, Palm Sugar, Powdered Sugar … (+5 more) |
| salt | 4 | Salt, Coarse Salt, Kosher Salt, Sea Salt |
| oil | 6 | Oil, Canola Oil, Rapeseed Oil, Sunflower Oil, Truffle Oil, High Heat Cooking Oil |
| chicken stock | 1 | Chicken Stock |
| chicken breast | 3 | Chicken Breast, Chicken Breasts, Chicken |
| ground beef | 3 | Ground Beef, Beef, Minced Beef |
| eggplant | 3 | Aubergine, Egg Plants, Baby Aubergine |
| yoghurt | 4 | Yogurt, Greek Yogurt, Natural Yoghurt, Strained Yoghurt |

## Deliberately excluded

| Fridge item | Not in its family | Why |
|---|---|---|
| cream | Coconut Cream, Ice Cream, Sour Cream, Cream Cheese | coconut is a food; ice, sour cream and cream cheese are different products |
| potato | Sweet Potatoes, Potato Starch | sweet potatoes are a different vegetable; starch has a different head noun |
| chicken | Chicken Stock, Chicken Liver | different head noun (stock, liver) |
| onion | Spring Onions, Onion Salt | spring onions are a different vegetable; onion salt has a different head noun |
| pepper | Red/Green/Yellow Pepper, Cayenne Pepper, Black Pepper | the vegetable and the other spices are different products (black pepper is its own staple) |
| milk | Coconut Milk, Condensed Milk, Soya Milk | coconut and soya are foods; condensed milk is a different product |
| butter | Peanut Butter, Palm Butter | peanut is a food; palm butter is a different product |
